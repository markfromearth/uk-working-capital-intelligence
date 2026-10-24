"""
Unit tests for Stage 3: Post-Gate Intelligence Refinement Agent & Primary Source Attribution.
Verifies justified score increases/decreases and primary source retrievability.
"""

from datetime import datetime, timezone
import pytest
from backend.app.schemas.warden import BalanceSheetData, ScreeningResult
from backend.app.services.refinement_agent import IntelligenceRefinementAgent


def test_refinement_score_increase_for_seasoned_high_coverage():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    screening = ScreeningResult(
        crn="08421099",
        company_name="TITAN HAULAGE LIMITED",
        sic_codes=["49410"],
        is_b2b_eligible=True,
        provisional_score=70.0,
        passed_gate=True,
        knockout_applied=False,
    )

    balance_sheet = BalanceSheetData(
        extraction_path="IXBRL",
        accounting_framework="FRS_102",
        current_assets_total=600000.0,
        debtors=450000.0,
        cash_at_bank=50000.0,
        creditors_due_within_one_year=200000.0,  # 450k / 200k = 2.25x coverage (High cover)
        net_current_assets=400000.0,
        is_arithmetic_valid=True,
    )

    profile = {
        "company_name": "TITAN HAULAGE LIMITED",
        "date_of_creation": "2015-03-20",  # 11 years seasoned
    }
    charges = []
    filings = []
    officers = [
        {"name": "SMITH, John", "officer_role": "director", "appointed_on": "2015-03-20"},
        {"name": "SMITH, Paul", "officer_role": "director", "appointed_on": "2017-06-10"},
    ]

    refined_score, adjustments, sources = IntelligenceRefinementAgent.refine_provisional_score(
        screening=screening,
        balance_sheet=balance_sheet,
        profile=profile,
        charges=charges,
        filings=filings,
        officers=officers,
        as_of=now,
    )

    # Base score: 70.0 -> Should increase
    assert refined_score > 70.0
    assert any(a.direction == "INCREASE" and a.metric_name == "debtor_to_creditor_coverage" for a in adjustments)
    assert any(a.direction == "INCREASE" and a.metric_name == "trading_seasoning_stability" for a in adjustments)
    assert any(a.direction == "INCREASE" and a.metric_name == "board_governance_stability" for a in adjustments)

    # Verify primary sources are retrievable
    assert len(sources) >= 3
    for s in sources:
        assert s.uri != ""
        assert s.source_type != ""


def test_refinement_score_decrease_for_young_creditor_overhang():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    screening = ScreeningResult(
        crn="12345678",
        company_name="EARLY FREIGHT LTD",
        sic_codes=["49410"],
        is_b2b_eligible=True,
        provisional_score=75.0,
        passed_gate=True,
        knockout_applied=False,
    )

    # Debtors 150k vs Creditors 300k (0.50x coverage - severe overhang)
    balance_sheet = BalanceSheetData(
        extraction_path="VISION_PDF",
        accounting_framework="FRS_102",
        current_assets_total=200000.0,
        debtors=150000.0,
        cash_at_bank=20000.0,
        creditors_due_within_one_year=300000.0,
        net_current_assets=-100000.0,
        is_arithmetic_valid=True,
    )

    profile = {
        "company_name": "EARLY FREIGHT LTD",
        "date_of_creation": "2025-08-01",  # ~14 months old (young enterprise)
    }

    refined_score, adjustments, sources = IntelligenceRefinementAgent.refine_provisional_score(
        screening=screening,
        balance_sheet=balance_sheet,
        profile=profile,
        charges=[],
        filings=[],
        officers=[],
        as_of=now,
    )

    # Base score: 75.0 -> Should decrease
    assert refined_score < 75.0
    assert any(a.direction == "DECREASE" and a.metric_name == "creditor_overhang_drag" for a in adjustments)
    assert any(a.direction == "DECREASE" and a.metric_name == "young_enterprise_discount" for a in adjustments)


def test_refinement_score_aa01_and_sh01_adjustments():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    screening = ScreeningResult(
        crn="08421099",
        company_name="TITAN HAULAGE LIMITED",
        sic_codes=["49410"],
        is_b2b_eligible=True,
        provisional_score=70.0,
        passed_gate=True,
        knockout_applied=False,
    )
    balance_sheet = BalanceSheetData(
        extraction_path="IXBRL",
        accounting_framework="FRS_102",
        current_assets_total=600000.0,
        debtors=450000.0,
        cash_at_bank=50000.0,
        creditors_due_within_one_year=200000.0,
        net_current_assets=400000.0,
        is_arithmetic_valid=True,
        period_end_date="2025-01-01",  # ~21.7 months old (stale)
        balance_sheet_age_months=21.7,
        seasoning_status="STALE",
    )
    profile = {
        "company_name": "TITAN HAULAGE LIMITED",
        "date_of_creation": "2015-03-20",
    }
    filings = [
        {"type": "AA01", "date": "2026-08-15"},  # AA01 date extension distress
        {"type": "SH01", "date": "2026-09-01"},  # Fresh equity capital injection
    ]

    refined_score, adjustments, sources = IntelligenceRefinementAgent.refine_provisional_score(
        screening=screening,
        balance_sheet=balance_sheet,
        profile=profile,
        charges=[],
        filings=filings,
        officers=[],
        as_of=now,
    )

    assert any(a.metric_name == "accounting_date_extension_distress" and a.delta_points == -5.0 for a in adjustments)
    assert any(a.metric_name == "mid_year_equity_injection" and a.delta_points == 4.0 for a in adjustments)
    assert any(a.metric_name == "accounts_seasoning_latency_discount" and a.delta_points == -3.0 for a in adjustments)
    assert any(s.source_type == "COMPANIES_HOUSE_FILING" and "AA01" in s.source_id for s in sources)

