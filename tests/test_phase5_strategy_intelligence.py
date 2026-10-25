"""
Phase 5 Verification Suite:
Intelligence Quality, Campaign Strategy Evidence Grounding & Confidence Contracts.
"""

import pytest
from backend.app.services.campaign_strategy import CampaignStrategyService


def test_no_fabricated_contract_award_when_indicator_absent():
    """
    Phase 5 Mandatory Negative Test:
    When 'contract_award_growth' is NOT in triggered_indicators, the service MUST NOT
    generate a Public Framework & Procurement Winners contract mobilization strategy.
    Previously, line 49 had `if "contract_award_growth" in triggered_indicators or True:`.
    """
    service = CampaignStrategyService()
    strategies = service.generate_strategies(
        company_name="APEX PRECISION ENGINEERING LTD",
        crn="08931201",
        triggered_indicators=["white_space"],
        indicative_facility_gbp=300_000.0,
    )
    assert len(strategies) == 1
    assert strategies[0].finance_product == "Confidential Invoice Discounting"
    assert not any("Mobilization" in s.finance_product for s in strategies)
    assert not any("procurement award" in s.commercial_hypothesis.lower() for s in strategies)


def test_empty_indicators_returns_zero_strategies():
    """
    Phase 5 Mandatory Negative Test:
    When triggered_indicators is empty, zero strategies may be fabricated.
    """
    service = CampaignStrategyService()
    strategies = service.generate_strategies(
        company_name="EMPTY SIGNALS LTD",
        crn="08931201",
        triggered_indicators=[],
    )
    assert strategies == []


def test_white_space_blocked_when_conflicting_active_charges_present():
    """
    Phase 5 Mandatory Negative Test:
    A company with both a satisfied charge ('white_space') AND active charges
    (has_active_charges=True or active_charge_count > 0) MUST NOT be pitched as having
    an unencumbered debtor book.
    """
    service = CampaignStrategyService()
    strategies = service.generate_strategies(
        company_name="ENCUMBERED HAULAGE LTD",
        crn="08931201",
        triggered_indicators=["white_space", "refinance_window"],
        has_active_charges=True,
        active_charge_count=2,
    )
    products = [s.finance_product for s in strategies]
    assert "Confidential Invoice Discounting" not in products
    assert "Competitive Refinance & Restructure" in products


def test_white_space_strategy_reflects_21_day_registration_lag_and_invalidation():
    """
    Verify white-space strategy explicitly separates observed facts, derived features,
    hypothesis, and invalidation criteria including the 21-day CA 2006 s.859A filing lag.
    """
    service = CampaignStrategyService()
    strategies = service.generate_strategies(
        company_name="CLEAN LEDGER LTD",
        crn="08931201",
        triggered_indicators=["white_space"],
        has_active_charges=False,
        active_charge_count=0,
    )
    assert len(strategies) == 1
    strat = strategies[0]
    assert len(strat.observed_facts) >= 2
    assert len(strat.derived_features) >= 2
    assert len(strat.invalidation_criteria) >= 2
    assert any("21-day" in ev or "21 days" in ev for ev in strat.supporting_evidence + strat.invalidation_criteria)
    assert strat.confidence_contract.verification_status == "VERIFIED"
    assert 0.5 < strat.confidence_score < 0.95


def test_simulated_or_stale_evidence_penalizes_confidence_contract():
    """
    Verify that simulated or stale data lowers the composite confidence score relative
    to verified, current primary evidence.
    """
    service = CampaignStrategyService()
    verified_strats = service.generate_strategies(
        company_name="VERIFIED CO LTD",
        crn="08931201",
        triggered_indicators=["contract_award_growth"],
        verification_status="VERIFIED",
        data_freshness_status="CURRENT",
    )
    simulated_strats = service.generate_strategies(
        company_name="SIMULATED CO LTD",
        crn="08931201",
        triggered_indicators=["contract_award_growth"],
        verification_status="SIMULATED",
        data_freshness_status="STALE",
    )
    assert verified_strats[0].confidence_score > simulated_strats[0].confidence_score
    assert simulated_strats[0].confidence_contract.verification_status == "SIMULATED"
    assert simulated_strats[0].confidence_score < 0.55
