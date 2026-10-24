"""
Tests for Campaign Economics Service, Unit Economics Modeling, and Agent G.
Validates multi-scenario bounds, break-even response rates, risk-bounded test plans,
and strict assumption isolation.
"""

import pytest
from backend.app.agents.campaign_economics import CampaignEconomicsAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.schemas.marketing import ScenarioType
from backend.app.services.campaign_economics import CampaignEconomicsService


def test_scenario_bounds_ordering():
    """Verify Conservative < Base < Optimistic for conversion rates and revenue."""
    facility = 400_000.0  # £400k invoice finance facility
    cost = 1_000.0        # £1k campaign test
    reach = 2_000

    model = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=facility,
        campaign_cost_gbp=cost,
        target_reach=reach,
    )

    cons = model.scenarios[ScenarioType.CONSERVATIVE.value]
    base = model.scenarios[ScenarioType.BASE.value]
    opt = model.scenarios[ScenarioType.OPTIMISTIC.value]

    # Verify strictly ordered conversion rates
    assert cons.engagement_rate < base.engagement_rate < opt.engagement_rate
    assert cons.qualified_lead_rate < base.qualified_lead_rate < opt.qualified_lead_rate
    assert cons.application_rate < base.application_rate < opt.application_rate
    assert cons.funding_approval_rate < base.funding_approval_rate < opt.funding_approval_rate

    # Verify gross commission order
    assert cons.gross_commission_gbp < base.gross_commission_gbp < opt.gross_commission_gbp
    assert cons.net_value_gbp < base.net_value_gbp < opt.net_value_gbp

    # Verify probability-weighted expected net value is between conservative and optimistic
    assert cons.net_value_gbp <= model.expected_net_value_gbp <= opt.net_value_gbp


def test_break_even_and_test_budget_math():
    """Verify break-even response rates and risk-bounded test limits."""
    facility = 250_000.0
    cost = 1_500.0
    reach = 5_000

    model = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=facility,
        campaign_cost_gbp=cost,
        target_reach=reach,
    )

    assert model.break_even_response_rate > 0.0
    assert model.break_even_qualified_leads > 0.0
    assert model.max_recommended_test_budget_gbp > 0.0
    assert model.max_recommended_test_budget_gbp <= cost

    # Test zero-cost campaign handling
    zero_cost_model = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=facility,
        campaign_cost_gbp=0.0,
        target_reach=reach,
    )
    assert zero_cost_model.break_even_response_rate == 0.0
    assert zero_cost_model.break_even_qualified_leads == 0.0
    assert zero_cost_model.scenarios[ScenarioType.BASE.value].roi_multiple == 999.0
    assert zero_cost_model.max_recommended_test_budget_gbp <= 500.0


def test_assumption_isolation_and_empirical_calibration():
    """Verify assumptions log explicitly isolates unobserved benchmarks from observed conversions."""
    model_unobserved = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=300_000.0,
        campaign_cost_gbp=500.0,
        is_historical_benchmark=False,
    )
    assert model_unobserved.is_historical_benchmark is False
    assert any("unobserved for target firm" in a.lower() for a in model_unobserved.assumptions_log)

    # Now calibrate with empirical observed rates
    empirical_rates = {
        "engagement_rate": 0.02,
        "qualified_lead_rate": 0.10,
    }
    model_observed = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=300_000.0,
        campaign_cost_gbp=500.0,
        is_historical_benchmark=True,
        observed_rates=empirical_rates,
    )
    assert model_observed.is_historical_benchmark is True
    assert any("empirical historical conversions" in a.lower() for a in model_observed.assumptions_log)
    # Base scenario engagement rate calibrated to empirical 0.02
    assert model_observed.scenarios[ScenarioType.BASE.value].engagement_rate == 0.02


@pytest.mark.asyncio
async def test_campaign_economics_agent_execution():
    """Verify Agent G executes cleanly through typed envelope contract."""
    agent = CampaignEconomicsAgent()
    assert agent.role == AgentRole.CAMPAIGN_ECONOMICS

    req = AgentTaskRequest(
        target_role=AgentRole.CAMPAIGN_ECONOMICS,
        crn="08931201",
        budget_limit_gbp=0.00,
        input_payload={
            "facility_size_gbp": 500_000.0,
            "campaign_cost_gbp": 0.0,
            "target_reach": 1500,
            "strategy_id": "STRAT-TEST-001",
        },
    )

    res = await agent.execute(req)
    assert res.status == AgentTaskStatus.COMPLETED
    assert res.cost_incurred_gbp == 0.00
    assert "model_id" in res.output_payload
    assert res.output_payload["indicative_facility_size_gbp"] == 500_000.0
    assert "CONSERVATIVE" in res.output_payload["scenarios"]
    assert "BASE" in res.output_payload["scenarios"]
    assert "OPTIMISTIC" in res.output_payload["scenarios"]
    assert len(res.evidence_references) >= 1
