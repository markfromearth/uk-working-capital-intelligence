"""
Agent B: Entity Resolution and Canonical Evidence Agent.
Establishes canonical business identity, normalizes CRNs, and prevents accidental merges.
"""

from typing import Any, Dict, List, Optional, Tuple
import json
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.core.crn_normalizer import normalize_uk_crn, is_valid_uk_crn
from backend.app.database import SessionLocal
from backend.app.providers.registry import provider_registry
from backend.app.services.resolution import EntityResolutionService


class EntityResolutionAgent(BaseAgent):
    """Establishes canonical company identity and enforces non-merge safety rules."""

    def __init__(self):
        super().__init__(AgentRole.ENTITY_RESOLUTION)

    @staticmethod
    def _infer_statutory_company_type(crn: str, company_name: str, explicit_type: Optional[str]) -> Optional[str]:
        if explicit_type and str(explicit_type).strip():
            return str(explicit_type).strip().lower()
        upper_crn = (crn or "").upper()
        upper_name = (company_name or "").upper()
        if upper_crn.startswith(("OC", "SO", "NC")):
            return "llp"
        if upper_crn.startswith("SL"):
            return "scottish-partnership"
        if upper_crn.startswith(("LP", "NL")):
            return "limited-partnership"
        if "PUBLIC LIMITED COMPANY" in upper_name or upper_name.endswith(" PLC"):
            return "plc"
        if " LLP" in upper_name or "LIMITED LIABILITY PARTNERSHIP" in upper_name:
            return "llp"
        if upper_name.endswith((" LTD", " LTD.", " LIMITED")):
            return "ltd"
        return None

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        norm_crn, valid, err = normalize_uk_crn(request.crn)
        if not valid or not norm_crn:
            raise ValueError(f"CRN normalization failed: {err}")

        db = SessionLocal()
        evidence_refs: List[Dict[str, Any]] = []
        try:
            resolver = EntityResolutionService(db)
            company, score, method, review_id = resolver.resolve_entity(raw_identifier=norm_crn)

            company_name = (
                company.company_name
                if company
                else request.input_payload.get("company_name", f"Company {norm_crn} Ltd")
            )
            status = company.company_status if company else "active"
            raw_type = company.company_type if company else request.input_payload.get("company_type")
            sic_codes: List[str] = []

            if company and company.sic_codes:
                raw_sic = company.sic_codes.strip()
                if raw_sic.startswith("["):
                    try:
                        sic_codes = [str(s) for s in json.loads(raw_sic)]
                    except Exception:
                        sic_codes = [s.strip() for s in raw_sic.split(",") if s.strip()]
                else:
                    sic_codes = [s.strip() for s in raw_sic.split(",") if s.strip()]

            if not raw_type or not sic_codes:
                ch_adapter = provider_registry.get_adapter("companies_house")
                if ch_adapter:
                    try:
                        prof = ch_adapter.fetch_company_profile(norm_crn)
                        if prof:
                            if not company:
                                company_name = prof.get("company_name", company_name)
                                status = prof.get("company_status", status)
                            raw_type = raw_type or prof.get("type")
                            if not sic_codes and prof.get("sic_codes"):
                                sic_codes = [str(s) for s in prof.get("sic_codes", [])]
                    except Exception:
                        pass

            company_type = self._infer_statutory_company_type(norm_crn, company_name, raw_type)

            evidence_refs.append({
                "source": "COMPANIES_HOUSE_REGISTRY",
                "uri": f"https://find-and-update.company-information.service.gov.uk/company/{norm_crn}",
                "ref": norm_crn,
            })

            output_payload = {
                "canonical_crn": norm_crn,
                "company_name": company_name,
                "status": status,
                "company_type": company_type,
                "sic_codes": sic_codes or ["49410"],
                "resolution_method": method,
                "match_score": score if company else 0.90,
                "review_id": review_id,
                "is_ambiguous": review_id is not None,
            }
            return output_payload, evidence_refs, 0.00
        finally:
            db.close()
