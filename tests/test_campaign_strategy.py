"""
Test Suite for Campaign Strategy Generation and Audience Strategy Agent (Agent F).
Verifies differentiated B2B strategies, compliance guardrails, validation test plans,
and multi-scenario hypothesis formulation.
"""

import pytest
from backend.app.schemas.marketing import ContentFormat, CampaignStrategy
from backend.app.services.campaign_strategy import CampaignStrategyService
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.agents.audience_strategy import AudienceStrategyAgent


def test_strategy_generation_differentiates_scenarios():
    service = CampaignStrategyService()
    company_name = "TITAN HAULAGE LIMITED"
    crn = "08421099"

    # Multi-indicator scenario: White Space + Contract Win + Refinance Window
    indicators = ["contract_award_growth", "white_space", "refinance_window"]
    strategies = service.generate_strategies(
        company_name=company_name,
        crn=crn,
        triggered_indicators=indicators,
        indicative_facility_gbp=500_000.0,
    )

    assert len(strategies) >= 3

    # 1. Contract Mobilization Strategy
    strat_contract = next(s for s in strategies if "Mobilization" in s.finance_product)
    assert "Managing Director" in strat_contract.target_role
    assert strat_contract.content_format == ContentFormat.ASSOCIATION_WEBINAR
    assert "procurement award" in strat_contract.commercial_hypothesis.lower()
    assert strat_contract.validation_test.duration_weeks == 3

    # 2. White Space Strategy
    strat_ws = next(s for s in strategies if "Invoice Discounting" in s.finance_product)
    assert "Finance Director" in strat_ws.target_role
    assert strat_ws.content_format == ContentFormat.PODCAST_SPONSORSHIP
    assert "Form MR04 satisfaction" in strat_ws.supporting_evidence[0]

    # 3. Refinance Strategy
    strat_refin = next(s for s in strategies if "Refinance" in s.finance_product)
    assert "Finance Director" in strat_refin.target_role
    assert strat_refin.content_format == ContentFormat.TRADE_PUBLICATION_ARTICLE
    assert "36 months" in strat_refin.commercial_hypothesis


def test_strategy_compliance_guardrails():
    service = CampaignStrategyService()
    strategies = service.generate_strategies(
        company_name="APEX PRECISION ENGINEERING LTD",
        crn="08931201",
        triggered_indicators=["white_space"],
    )

    strat = strategies[0]
    guardrails = strat.compliance_guardrails
    assert len(guardrails) >= 3
    assert any("PECR" in g for g in guardrails)
    assert any("FCA" in g for g in guardrails)
    assert any("Data Subject Rights" in g for g in guardrails)


@pytest.mark.asyncio
async def test_audience_strategy_agent_execution():
    agent = AudienceStrategyAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.AUDIENCE_STRATEGY,
        crn="01989005",
        budget_limit_gbp=0.00,
        input_payload={
            "company_name": "CARTER STEELWORK LTD",
            "indicators": ["contract_award_growth", "white_space"],
            "facility_size_gbp": 750_000.0,
        },
    )

    result = await agent.execute(req)
    assert result.status == AgentTaskStatus.COMPLETED
    assert result.role == AgentRole.AUDIENCE_STRATEGY
    assert result.cost_incurred_gbp == 0.00
    assert result.output_payload["strategies_count"] >= 2
    assert result.output_payload["primary_recommended_strategy_id"] is not None
    assert len(result.evidence_references) >= 2
