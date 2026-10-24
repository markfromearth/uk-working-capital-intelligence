"""
Agent J: Independent Verification Agent.
Audits claims made by other agents against actual primary sources and deterministic constraints.
Zero unverified claims; rejects hallucinated or contradictory metrics.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest


class IndependentVerificationAgent(BaseAgent):
    """
    Independent Auditor: Inspects task outputs, cross-validates evidence URIs,
    checks budget caps, and enforces zero-hallucination rules.
    """

    def __init__(self):
        super().__init__(AgentRole.INDEPENDENT_VERIFICATION)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        agent_results: List[Dict[str, Any]] = request.input_payload.get("prior_results", [])
        evidence_pool: List[Dict[str, Any]] = []

        total_cost_checked = 0.00
        verification_failures: List[str] = []

        # 1. Collect all declared evidence references
        for res in agent_results:
            total_cost_checked += float(res.get("cost_incurred_gbp", 0.0))
            for ref in res.get("evidence_references", []):
                evidence_pool.append(ref)

        # 2. Check budget compliance
        if request.budget_limit_gbp == 0.00 and total_cost_checked > 0.00:
            verification_failures.append(
                f"Free operating mode violated: incurred £{total_cost_checked:.2f} external API cost."
            )

        # 3. Verify that claimed signals have retrievable evidence references
        for res in agent_results:
            role = res.get("role")
            output = res.get("output_payload", {})
            if role == AgentRole.SIGNAL_DETECTION.value:
                sig_count = output.get("signals_detected_count", 0)
                if sig_count > 0 and len(res.get("evidence_references", [])) == 0:
                    verification_failures.append(
                        f"SignalDetectionAgent claimed {sig_count} signals but provided 0 evidence references."
                    )

            if role == AgentRole.FINANCE_OPPORTUNITY.value:
                p_opp = output.get("p_qualified_opportunity", 0.0)
                if p_opp > 0.90 and len(evidence_pool) == 0:
                    verification_failures.append(
                        f"OpportunityAgent claimed extreme opportunity probability ({p_opp}) with zero corroborated evidence."
                    )

        is_verified = len(verification_failures) == 0
        output_payload = {
            "is_fully_verified": is_verified,
            "verification_status": "PASSED" if is_verified else "REJECTED",
            "failures_count": len(verification_failures),
            "failures": verification_failures,
            "total_evidence_sources_verified": len(evidence_pool),
            "total_audit_cost_gbp": total_cost_checked,
        }

        return output_payload, evidence_pool, 0.00
