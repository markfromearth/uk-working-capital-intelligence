"""
Agent B: Entity Resolution and Canonical Evidence Agent.
Establishes canonical business identity, normalizes CRNs, and prevents accidental merges.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.core.crn_normalizer import normalize_uk_crn, is_valid_uk_crn
from backend.app.database import SessionLocal
from backend.app.services.resolution import EntityResolutionService


class EntityResolutionAgent(BaseAgent):
    """Establishes canonical company identity and enforces non-merge safety rules."""

    def __init__(self):
        super().__init__(AgentRole.ENTITY_RESOLUTION)

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

            company_name = company.company_name if company else request.input_payload.get("company_name", f"Company {norm_crn}")
            status = company.company_status if company else "active"

            evidence_refs.append({
                "source": "COMPANIES_HOUSE_REGISTRY",
                "uri": f"https://find-and-update.company-information.service.gov.uk/company/{norm_crn}",
                "ref": norm_crn,
            })

            output_payload = {
                "canonical_crn": norm_crn,
                "company_name": company_name,
                "status": status,
                "resolution_method": method,
                "match_score": score,
                "review_id": review_id,
                "is_ambiguous": review_id is not None,
            }
            return output_payload, evidence_refs, 0.00
        finally:
            db.close()
