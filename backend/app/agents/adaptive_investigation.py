"""
Agent C: Adaptive Investigation Agent.
Determines whether additional permitted sources are economically justified using EVOI logic.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.database import SessionLocal
from backend.app.services.investigation_engine import ValueOfInformationEngine


class AdaptiveInvestigationAgent(BaseAgent):
    """Enforces Value-of-Information (EVOI) discipline and respects investigation budgets."""

    def __init__(self):
        super().__init__(AgentRole.ADAPTIVE_INVESTIGATION)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        db = SessionLocal()
        evidence_refs: List[Dict[str, Any]] = []
        cost_incurred = 0.00
        try:
            engine = ValueOfInformationEngine(db)
            decisions = engine.evaluate_investigation_opportunities(
                crn=request.crn,
                company_daily_budget_gbp=request.budget_limit_gbp,
            )

            investigate_count = sum(1 for d in decisions if d.decision == "INVESTIGATE")
            defer_count = sum(1 for d in decisions if d.decision == "DEFER")
            stop_count = sum(1 for d in decisions if d.decision == "STOP")

            dec_summaries = [
                {
                    "tool": d.tool_name,
                    "decision": d.decision,
                    "evoi_net": d.evoi_value,
                    "cost_gbp": d.estimated_cost_gbp,
                    "rationale": d.decision_rationale,
                }
                for d in decisions
            ]

            output_payload = {
                "total_tools_evaluated": len(decisions),
                "investigate_count": investigate_count,
                "defer_count": defer_count,
                "stop_count": stop_count,
                "decisions": dec_summaries,
            }

            return output_payload, evidence_refs, cost_incurred
        finally:
            db.close()
