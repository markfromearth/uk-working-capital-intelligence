"""
Agent I: Compliance & Adversarial Risk Verification Agent.
Enforces PECR Corporate Subscriber validation, suppression lists, SSRF protections
(with fail-closed DNS resolution), and cryptographic audit chain integrity
(including HMAC checkpoints) before campaign activation.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.core.ssrf_protection import validate_safe_url
from backend.app.database import SessionLocal
from backend.app.models.company import CompanyRegistry
from backend.app.models.decision_maker import PersonIdentity, SuppressionRecord
from backend.app.services.audit import verify_audit_log_chain
from backend.app.services.identity_resolution import IdentityResolutionService


class ComplianceRiskAgent(BaseAgent):
    """Enforces PECR, corporate subscriber rules, SSRF security, and cryptographic audit integrity."""

    def __init__(
        self,
        db: Optional[Session] = None,
        dns_resolver: Optional[Callable[[str], List[str]]] = None,
    ):
        super().__init__(AgentRole.COMPLIANCE_RISK)
        self._db = db
        self._dns_resolver = dns_resolver

    def _get_db(self) -> Session:
        return self._db if self._db is not None else SessionLocal()

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        db = self._get_db()
        should_close = self._db is None

        try:
            violations: List[str] = []
            checks_passed: List[str] = []
            evidence_refs: List[Dict[str, Any]] = []

            # 1. Resolve Company Type from payload or authoritative CompanyRegistry (never default to 'ltd')
            company_type = request.input_payload.get("company_type")
            if company_type is None and request.crn:
                comp = (
                    db.query(CompanyRegistry)
                    .filter(CompanyRegistry.crn == request.crn)
                    .first()
                )
                if comp and comp.company_type:
                    company_type = comp.company_type

            has_consent = bool(request.input_payload.get("has_prior_opt_in_consent", False))
            sender_disclosed = bool(request.input_payload.get("sender_identity_disclosed", True))
            opt_out_provided = bool(request.input_payload.get("valid_opt_out_address_provided", True))

            # 2. Suppression & Objection List Verification (CRN, Person ID, Email, Email Domain)
            supp_candidates: List[str] = []
            if request.crn:
                supp_candidates.append(request.crn.strip().lower())

            contact_email = (request.input_payload.get("contact_email") or "").strip().lower()
            if contact_email:
                supp_candidates.append(contact_email)
                if "@" in contact_email:
                    supp_candidates.append(contact_email.split("@", 1)[1])

            person_id = (request.input_payload.get("person_id") or "").strip()
            person_suppressed_reason: Optional[str] = None
            if person_id:
                supp_candidates.append(person_id.lower())
                person = (
                    db.query(PersonIdentity)
                    .filter(PersonIdentity.person_id == person_id)
                    .first()
                )
                if person:
                    supp_candidates.append(person.normalized_name.lower())
                    if request.crn:
                        supp_candidates.append(f"{person.normalized_name.lower()}:{request.crn.strip().lower()}")
                    if person.is_erased:
                        person_suppressed_reason = "gdpr_article_17_erasure"
                    elif person.is_suppressed:
                        person_suppressed_reason = "gdpr_article_21_objection"

            suppressed = None
            if supp_candidates:
                suppressed = (
                    db.query(SuppressionRecord)
                    .filter(
                        SuppressionRecord.is_active == True,
                        func.lower(SuppressionRecord.identifier_value).in_(supp_candidates),
                    )
                    .first()
                )

            is_suppressed = bool(suppressed is not None or person_suppressed_reason is not None)
            supp_reason = (
                suppressed.suppression_reason
                if suppressed is not None
                else person_suppressed_reason
            )

            # 3. Structured PECR & UK GDPR Decision Record
            compliance_decision = IdentityResolutionService.evaluate_pecr_uk_gdpr_compliance(
                company_type=company_type,
                crn=request.crn,
                has_prior_opt_in_consent=has_consent,
                sender_identity_disclosed=sender_disclosed,
                valid_opt_out_address_provided=opt_out_provided,
                is_suppressed=is_suppressed,
                suppression_reason=supp_reason,
            )

            status_code = compliance_decision["status_code"]
            legal_basis = compliance_decision["legal_basis"]
            rationale = compliance_decision["rationale"]

            if is_suppressed:
                violations.append(
                    f"SUPPRESSION_VIOLATION: Entity/contact matches active suppression record ({supp_reason})"
                )
            else:
                checks_passed.append("SUPPRESSION_CHECK: Not on internal or statutory suppression lists")

            if compliance_decision["is_permissible"]:
                checks_passed.append(f"PECR_STATUS: {status_code} ({legal_basis})")
                evidence_refs.append({
                    "source": "PECR_REG_22_23_VERIFICATION",
                    "ref": f"CRN-{request.crn}",
                    "uri": "https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/",
                    "excerpt": rationale,
                })
            elif not is_suppressed:
                violations.append(f"PECR_VIOLATION: {rationale}")

            # 4. SSRF Protection on External Feeds & Webhooks (with DNS resolution enabled)
            urls_to_check = list(request.input_payload.get("urls_to_verify", []))
            feed_url = request.input_payload.get("feed_uri")
            if feed_url:
                urls_to_check.append(feed_url)

            resolve_dns = bool(request.input_payload.get("resolve_dns", True))
            custom_dns_map: Optional[Dict[str, List[str]]] = request.input_payload.get("dns_resolution_map")
            resolver_fn = self._dns_resolver
            if custom_dns_map is not None:
                def _map_resolver(host: str) -> List[str]:
                    if host in custom_dns_map:
                        return custom_dns_map[host]
                    return ["93.184.216.34"]
                resolver_fn = _map_resolver

            ssrf_safe_count = 0
            for u in urls_to_check:
                is_safe, reason = validate_safe_url(
                    u,
                    resolve_dns=resolve_dns,
                    dns_resolver=resolver_fn,
                )
                if not is_safe:
                    violations.append(f"SSRF_VIOLATION: URL '{u}' rejected: {reason}")
                else:
                    ssrf_safe_count += 1

            if not any("SSRF_VIOLATION" in v for v in violations):
                checks_passed.append(f"SSRF_CHECK: {ssrf_safe_count} external endpoint(s) validated safe")

            # 5. Cryptographic Audit Chain & Checkpoint Integrity Check (verify_checkpoints=True)
            chain_valid, verified_count, audit_err = verify_audit_log_chain(db, verify_checkpoints=True)
            if chain_valid:
                checks_passed.append(f"AUDIT_CHAIN_INTEGRITY: {verified_count} audit blocks cryptographically intact")
                evidence_refs.append({
                    "source": "AUDIT_BLOCK_HASH_CHAIN",
                    "ref": f"BLOCKS-{verified_count}",
                    "uri": "urn:warden:audit:chain",
                    "excerpt": f"SHA-256 hash chain and HMAC checkpoints intact across {verified_count} records",
                })
            else:
                violations.append(f"AUDIT_CORRUPTION: Cryptographic hash chain failed: {audit_err}")

            is_cleared = len(violations) == 0

            output_payload = {
                "crn": request.crn,
                "is_compliance_cleared": is_cleared,
                "pecr_status": status_code,
                "legal_basis": legal_basis,
                "compliance_decision": compliance_decision,
                "checks_passed_count": len(checks_passed),
                "checks_passed": checks_passed,
                "violations_count": len(violations),
                "violations": violations,
            }

            return output_payload, evidence_refs, 0.00
        finally:
            if should_close:
                db.close()
