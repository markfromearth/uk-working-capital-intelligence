"""
Agent F: Audience and Content Strategy Agent.
Synthesizes commercial circumstances and marketing channels into evidence-backed campaign strategies.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.services.campaign_strategy import CampaignStrategyService


class AudienceStrategyAgent(BaseAgent):
    """Generates targeted, differentiated campaign concepts, messages, and validation test plans."""

    def __init__(self):
        super().__init__(AgentRole.AUDIENCE_STRATEGY)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        company_name = request.input_payload.get("company_name", f"Company {request.crn}")
        indicators = request.input_payload.get("indicators", ["contract_award_growth", "white_space"])
        facility_size = float(request.input_payload.get("facility_size_gbp", 350_000.0))
        channels = request.input_payload.get("channels", [])

        service = CampaignStrategyService()
        strategies = service.generate_strategies(
            company_name=company_name,
            crn=request.crn,
            triggered_indicators=indicators,
            indicative_facility_gbp=facility_size,
            available_channels=channels,
        )

        evidence_refs: List[Dict[str, Any]] = []
        for s in strategies:
            for ev in s.supporting_evidence:
                evidence_refs.append({
                    "source": "STRATEGY_HYPOTHESIS",
                    "ref": s.strategy_id,
                    "uri": f"https://find-and-update.company-information.service.gov.uk/company/{request.crn}",
                    "excerpt": ev,
                })

        output_payload = {
            "crn": request.crn,
            "strategies_count": len(strategies),
            "strategies": [s.model_dump() for s in strategies],
            "primary_recommended_strategy_id": strategies[0].strategy_id if strategies else None,
        }

        return output_payload, evidence_refs, 0.00
