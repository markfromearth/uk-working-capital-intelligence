"""
Agent I: Compliance & Adversarial Risk Verification Agent.
Enforces PECR Corporate Subscriber validation, suppression lists, SSRF protections,
and cryptographic audit chain integrity before campaign activation.
"""

from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.core.ssrf_protection import validate_safe_url
from backend.app.database import SessionLocal
from backend.app.models.decision_maker import SuppressionRecord
from backend.app.services.audit import verify_audit_log_chain
from backend.app.services.identity_resolution import IdentityResolutionService


class ComplianceRiskAgent(BaseAgent):
    """Enforces PECR, corporate subscriber rules, SSRF security, and cryptographic audit integrity."""

    def __init__(self, db: Optional[Session] = None):
        super().__init__(AgentRole.COMPLIANCE_RISK)
        self._db = db

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

            # 1. PECR Corporate Subscriber Validation (Reg 22 vs Reg 23)
            company_type = request.input_payload.get("company_type", "ltd")
            has_consent = bool(request.input_payload.get("has_prior_opt_in_consent", False))

            is_corp_ok, status_code, legal_basis, rationale = (
                IdentityResolutionService.validate_corporate_subscriber_status(
                    company_type=company_type,
                    crn=request.crn,
                    has_prior_opt_in_consent=has_consent,
                )
            )

            if is_corp_ok:
                checks_passed.append(f"PECR_STATUS: {status_code} ({legal_basis})")
                evidence_refs.append({
                    "source": "PECR_REG_23_VERIFICATION",
                    "ref": f"CRN-{request.crn}",
                    "uri": "https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/",
                    "excerpt": rationale,
                })
            else:
                violations.append(f"PECR_VIOLATION: {rationale}")

            # 2. Suppression List Verification
            suppressed = db.query(SuppressionRecord).filter(
                (SuppressionRecord.identifier_value == request.crn)
                | (SuppressionRecord.identifier_value == request.input_payload.get("contact_email"))
            ).first()

            if suppressed:
                violations.append(
                    f"SUPPRESSION_VIOLATION: Entity/contact matches suppression record ({suppressed.suppression_reason})"
                )
            else:
                checks_passed.append("SUPPRESSION_CHECK: Not on internal or statutory suppression lists")

            # 3. SSRF Protection on External Feeds & Webhooks
            urls_to_check = request.input_payload.get("urls_to_verify", [])
            feed_url = request.input_payload.get("feed_uri")
            if feed_url:
                urls_to_check.append(feed_url)

            ssrf_safe_count = 0
            for u in urls_to_check:
                is_safe, reason = validate_safe_url(u, resolve_dns=False)
                if not is_safe:
                    violations.append(f"SSRF_VIOLATION: URL '{u}' rejected: {reason}")
                else:
                    ssrf_safe_count += 1

            if not any("SSRF_VIOLATION" in v for v in violations):
                checks_passed.append(f"SSRF_CHECK: {ssrf_safe_count} external endpoint(s) validated safe")

            # 4. Cryptographic Audit Chain Integrity Check
            chain_valid, verified_count, audit_err = verify_audit_log_chain(db, verify_checkpoints=False)
            if chain_valid:
                checks_passed.append(f"AUDIT_CHAIN_INTEGRITY: {verified_count} audit blocks cryptographically intact")
                evidence_refs.append({
                    "source": "AUDIT_BLOCK_HASH_CHAIN",
                    "ref": f"BLOCKS-{verified_count}",
                    "uri": "urn:warden:audit:chain",
                    "excerpt": f"SHA-256 hash chain intact across {verified_count} records",
                })
            else:
                violations.append(f"AUDIT_CORRUPTION: Cryptographic hash chain failed: {audit_err}")

            is_cleared = len(violations) == 0

            output_payload = {
                "crn": request.crn,
                "is_compliance_cleared": is_cleared,
                "pecr_status": status_code,
                "legal_basis": legal_basis,
                "checks_passed_count": len(checks_passed),
                "checks_passed": checks_passed,
                "violations_count": len(violations),
                "violations": violations,
            }

            return output_payload, evidence_refs, 0.00
        finally:
            if should_close:
                db.close()
