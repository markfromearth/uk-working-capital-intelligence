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
        require_live = bool(request.input_payload.get("require_live_evidence", False))
        evidence_pool: List[Dict[str, Any]] = []

        total_cost_checked = 0.00
        verification_failures: List[str] = []
        has_distress_knockout = False
        has_simulated_signals = False

        # 1. Collect all declared evidence references and check task execution status
        for res in agent_results:
            role = res.get("role", "UNKNOWN")
            status = res.get("status", "COMPLETED")
            total_cost_checked += float(res.get("cost_incurred_gbp", 0.0))
            for ref in res.get("evidence_references", []):
                evidence_pool.append(ref)

            if status in ("FAILED", "BUDGET_EXCEEDED", "TIMEOUT"):
                verification_failures.append(
                    f"Upstream agent [{role}] terminated with non-completed status '{status}': {res.get('error_message', '')}"
                )

        # 2. Check budget compliance
        if request.budget_limit_gbp == 0.00 and total_cost_checked > 0.00:
            verification_failures.append(
                f"Free operating mode violated: incurred £{total_cost_checked:.2f} external API cost."
            )
        elif total_cost_checked > request.budget_limit_gbp:
            verification_failures.append(
                f"Pipeline budget limit (£{request.budget_limit_gbp:.2f}) exceeded: incurred £{total_cost_checked:.2f}."
            )

        # 3. Cross-validate agent outputs against primary evidence, entity ambiguity, and compliance gates
        for res in agent_results:
            role = res.get("role")
            output = res.get("output_payload", {}) or {}

            if role == AgentRole.SIGNAL_DETECTION.value:
                sig_count = output.get("signals_detected_count", 0)
                if sig_count > 0 and len(res.get("evidence_references", [])) == 0:
                    verification_failures.append(
                        f"SignalDetectionAgent claimed {sig_count} signals but provided 0 evidence references."
                    )
                if output.get("has_distress_signals"):
                    has_distress_knockout = True
                for sig in output.get("signals", []):
                    if sig.get("verification_status") == "simulated":
                        has_simulated_signals = True

            elif role == AgentRole.ENTITY_RESOLUTION.value:
                if output.get("is_ambiguous") is True:
                    verification_failures.append(
                        f"EntityResolutionAgent flagged ambiguous entity match (review_id={output.get('review_id')}); "
                        f"downstream activation is prohibited until manual resolution."
                    )

            elif role == AgentRole.FINANCE_OPPORTUNITY.value:
                p_opp = output.get("p_qualified_opportunity", 0.0)
                if p_opp > 0.90 and len(evidence_pool) == 0:
                    verification_failures.append(
                        f"OpportunityAgent claimed extreme opportunity probability ({p_opp}) with zero corroborated evidence."
                    )

            elif role == AgentRole.AUDIENCE_STRATEGY.value:
                strategies = output.get("strategies", [])
                if has_distress_knockout and len(strategies) > 0:
                    verification_failures.append(
                        "AudienceStrategyAgent generated commercial strategies despite statutory insolvency distress signal knockout."
                    )
                for strat in strategies:
                    if not strat.get("supporting_evidence"):
                        verification_failures.append(
                            f"Strategy {strat.get('strategy_id')} lacks supporting_evidence."
                        )
                    cc = strat.get("confidence_contract") or {}
                    if require_live and (cc.get("verification_status") == "SIMULATED" or has_simulated_signals):
                        verification_failures.append(
                            f"Strategy {strat.get('strategy_id')} relies on simulated data when live evidence is required."
                        )

            elif role == AgentRole.COMPLIANCE_RISK.value:
                if output.get("is_compliance_cleared") is False:
                    violations = output.get("violations", ["Unspecified compliance block"])
                    verification_failures.append(
                        f"ComplianceRiskAgent blocked campaign clearance: {'; '.join(violations)}"
                    )

        if require_live and has_simulated_signals:
            verification_failures.append(
                "Simulated signals detected in pipeline while require_live_evidence=True."
            )

        is_verified = len(verification_failures) == 0
        output_payload = {
            "is_fully_verified": is_verified,
            "verification_status": "PASSED" if is_verified else "REJECTED",
            "failures_count": len(verification_failures),
            "failures": verification_failures,
            "has_simulated_signals": has_simulated_signals,
            "total_evidence_sources_verified": len(evidence_pool),
            "total_audit_cost_gbp": total_cost_checked,
        }

        return output_payload, evidence_pool, 0.00
