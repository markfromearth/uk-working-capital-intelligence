"""
Agent G: Campaign Economics & Full-Funnel Commercial Modeling Agent.
Quantifies unit economics, scenario bounds, break-even thresholds, and risk-bounded test plans.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.services.campaign_economics import CampaignEconomicsService


class CampaignEconomicsAgent(BaseAgent):
    """Models multi-scenario B2B funnel economics and calculates test budget boundaries."""

    def __init__(self):
        super().__init__(AgentRole.CAMPAIGN_ECONOMICS)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        facility_size = float(request.input_payload.get("facility_size_gbp", 350_000.0))
        campaign_cost = float(request.input_payload.get("campaign_cost_gbp", 0.0))
        target_reach = int(request.input_payload.get("target_reach", 1000))
        strategy_id = request.input_payload.get("strategy_id")
        is_historical = bool(request.input_payload.get("is_historical_benchmark", False))
        observed_rates = request.input_payload.get("observed_rates")

        model = CampaignEconomicsService.calculate_economics(
            facility_size_gbp=facility_size,
            campaign_cost_gbp=campaign_cost,
            target_reach=target_reach,
            strategy_id=strategy_id,
            is_historical_benchmark=is_historical,
            observed_rates=observed_rates,
        )

        evidence_refs: List[Dict[str, Any]] = [
            {
                "source": "COMMERCIAL_ECONOMICS_MODEL",
                "ref": model.model_id,
                "uri": "https://www.nacfb.org/resources/code-of-practice/",
                "excerpt": f"Expected net value: £{model.expected_net_value_gbp:,.2f} under uncertainty",
            }
        ]

        output_payload = {
            "crn": request.crn,
            "model_id": model.model_id,
            "strategy_id": model.strategy_id,
            "indicative_facility_size_gbp": model.indicative_facility_size_gbp,
            "expected_net_value_gbp": model.expected_net_value_gbp,
            "max_recommended_test_budget_gbp": model.max_recommended_test_budget_gbp,
            "break_even_response_rate": model.break_even_response_rate,
            "risk_assessment": model.risk_assessment,
            "scenarios": {k: v.model_dump() for k, v in model.scenarios.items()},
            "assumptions_log": model.assumptions_log,
            "is_historical_benchmark": model.is_historical_benchmark,
        }

        return output_payload, evidence_refs, 0.00
