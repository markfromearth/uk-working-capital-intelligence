"""
Central Multi-Agent Orchestrator.
Dispatches bounded tasks, manages dependency execution, enforces budgets,
prevents infinite loops, and records reproducible audit traces.
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
import uuid

from backend.app.agents.contracts import (
    AgentRole,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskStatus,
    MultiAgentExecutionTrace,
)
from backend.app.agents.signal_detection import SignalDetectionAgent
from backend.app.agents.entity_resolution import EntityResolutionAgent
from backend.app.agents.adaptive_investigation import AdaptiveInvestigationAgent
from backend.app.agents.opportunity_assessment import FinanceOpportunityAgent
from backend.app.agents.marketing_research import MarketingResearchAgent
from backend.app.agents.audience_strategy import AudienceStrategyAgent
from backend.app.agents.campaign_economics import CampaignEconomicsAgent
from backend.app.agents.independent_verification import IndependentVerificationAgent
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger


class MultiAgentOrchestrator:
    """Coordinates the specialized agent cluster across commercial intelligence workflows."""

    def __init__(self):
        self.signal_agent = SignalDetectionAgent()
        self.entity_agent = EntityResolutionAgent()
        self.investigation_agent = AdaptiveInvestigationAgent()
        self.opportunity_agent = FinanceOpportunityAgent()
        self.marketing_agent = MarketingResearchAgent()
        self.strategy_agent = AudienceStrategyAgent()
        self.economics_agent = CampaignEconomicsAgent()
        self.verification_agent = IndependentVerificationAgent()

    async def run_investigation_pipeline(
        self,
        crn: str,
        total_budget_gbp: float = 0.00,  # Strict £0.00 default for free mode
        correlation_id: Optional[str] = None,
    ) -> MultiAgentExecutionTrace:
        """
        Executes end-to-end multi-agent intelligence pipeline:
        Signal Detection (A) -> Entity Resolution (B) -> Adaptive Investigation (C) ->
        Finance Opportunity (D) -> Independent Verification (J).
        """
        cid = correlation_id or str(uuid.uuid4())
        norm_crn, valid, err = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            raise ValueError(f"Invalid CRN for multi-agent run: {err}")

        start_time = time.time()
        trace = MultiAgentExecutionTrace(
            crn=norm_crn,
            total_budget_gbp=total_budget_gbp,
        )

        remaining_budget = total_budget_gbp
        accumulated_cost = 0.00
        prior_results: List[AgentTaskResult] = []

        # --- STEP 1: Signal Detection (Agent A) ---
        req_a = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.SIGNAL_DETECTION,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
        )
        res_a = await self.signal_agent.execute(req_a)
        accumulated_cost += res_a.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_a.cost_incurred_gbp)
        prior_results.append(res_a)
        trace.task_traces.append(res_a)

        # --- STEP 2: Entity Resolution (Agent B) ---
        req_b = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.ENTITY_RESOLUTION,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={"signals": res_a.output_payload.get("signals", [])},
            provenance_chain=[res_a.task_id],
        )
        res_b = await self.entity_agent.execute(req_b)
        accumulated_cost += res_b.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_b.cost_incurred_gbp)
        prior_results.append(res_b)
        trace.task_traces.append(res_b)
        trace.company_name = res_b.output_payload.get("company_name")

        # --- STEP 3: Adaptive Investigation (Agent C) ---
        req_c = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.ADAPTIVE_INVESTIGATION,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={"company_name": trace.company_name},
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_c = await self.investigation_agent.execute(req_c)
        accumulated_cost += res_c.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_c.cost_incurred_gbp)
        prior_results.append(res_c)
        trace.task_traces.append(res_c)

        # --- STEP 4: Finance Opportunity (Agent D) ---
        req_d = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.FINANCE_OPPORTUNITY,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={
                "company_name": trace.company_name,
                "signals": res_a.output_payload.get("signals", []),
                "decisions": res_c.output_payload.get("decisions", []),
            },
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_d = await self.opportunity_agent.execute(req_d)
        accumulated_cost += res_d.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_d.cost_incurred_gbp)
        prior_results.append(res_d)
        trace.task_traces.append(res_d)

        # --- STEP 5: Marketing Channel Research (Agent E) ---
        req_e = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.MARKETING_RESEARCH,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={
                "company_name": trace.company_name,
                "sic_codes": ["49410"],
                "sector_keyword": "commercial transport",
            },
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_e = await self.marketing_agent.execute(req_e)
        accumulated_cost += res_e.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_e.cost_incurred_gbp)
        prior_results.append(res_e)
        trace.task_traces.append(res_e)

        # --- STEP 6: Audience & Content Strategy (Agent F) ---
        req_f = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.AUDIENCE_STRATEGY,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={
                "company_name": trace.company_name,
                "indicators": ["contract_award_growth", "white_space"],
                "facility_size_gbp": res_d.output_payload.get("estimated_facility_size_gbp", 350_000.0),
                "channels": res_e.output_payload.get("channel_candidates", []),
            },
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_f = await self.strategy_agent.execute(req_f)
        accumulated_cost += res_f.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_f.cost_incurred_gbp)
        prior_results.append(res_f)
        trace.task_traces.append(res_f)

        # --- STEP 7: Campaign Economics & Modeling (Agent G) ---
        req_g = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.CAMPAIGN_ECONOMICS,
            crn=norm_crn,
            budget_limit_gbp=remaining_budget,
            input_payload={
                "facility_size_gbp": res_d.output_payload.get("estimated_facility_size_gbp", 350_000.0),
                "campaign_cost_gbp": 0.0,  # Zero-cost test validation default
                "target_reach": 1000,
                "strategy_id": res_f.output_payload.get("primary_recommended_strategy_id"),
            },
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_g = await self.economics_agent.execute(req_g)
        accumulated_cost += res_g.cost_incurred_gbp
        remaining_budget = max(0.0, remaining_budget - res_g.cost_incurred_gbp)
        prior_results.append(res_g)
        trace.task_traces.append(res_g)

        # --- STEP 8: Independent Verification (Agent J) ---
        req_j = AgentTaskRequest(
            correlation_id=cid,
            target_role=AgentRole.INDEPENDENT_VERIFICATION,
            crn=norm_crn,
            budget_limit_gbp=total_budget_gbp,
            input_payload={
                "prior_results": [r.model_dump() for r in prior_results],
            },
            provenance_chain=[r.task_id for r in prior_results],
        )
        res_j = await self.verification_agent.execute(req_j)
        trace.task_traces.append(res_j)

        # Finalize Execution Trace
        trace.total_cost_gbp = accumulated_cost
        trace.total_duration_ms = (time.time() - start_time) * 1000.0
        v_out = res_j.output_payload
        trace.is_fully_verified = v_out.get("is_fully_verified", False)
        if not trace.is_fully_verified:
            trace.rejection_reason = "; ".join(v_out.get("failures", ["Audit failed"]))

        logger.info(
            f"Multi-Agent pipeline completed for {norm_crn} in {trace.total_duration_ms:.1f}ms "
            f"(Verified: {trace.is_fully_verified}, Cost: £{trace.total_cost_gbp:.2f})"
        )
        return trace


# Global singleton instance
default_agent_orchestrator = MultiAgentOrchestrator()
