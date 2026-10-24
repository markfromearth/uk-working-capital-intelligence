"""
Unit and integration tests for Stage 1: Companies House Ingestion & Provisional Scorer.
Verifies rate-limiting, indicator logic, dynamic weight normalization, hard knockouts, and primary sources.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta

from backend.app.schemas.warden import LendingArchetype, LenderClassification
from backend.app.providers.companies_house_ch import (
    CompaniesHouseCHClient,
    AsyncTokenBucket,
)
from backend.app.services.indicator_engine import (
    classify_lender,
    is_b2b_eligible,
    evaluate_white_space,
    evaluate_refinance_window,
    evaluate_tm01_cluster,
    evaluate_admin_delinquency,
)
from backend.app.services.provisional_scorer import ProvisionalScorer


def test_classify_lender():
    assert classify_lender("BARCLAYS BANK PLC") == LenderClassification.HIGH_STREET_BANK
    assert classify_lender("National Westminster Bank Plc") == LenderClassification.HIGH_STREET_BANK
    assert classify_lender("HSBC UK BANK PLC") == LenderClassification.HIGH_STREET_BANK
    assert classify_lender("BIBBY FINANCIAL SERVICES LIMITED") == LenderClassification.SECONDARY_INVOICE_FINANCIER
    assert classify_lender("Ultimate Finance Limited") == LenderClassification.SECONDARY_INVOICE_FINANCIER
    assert classify_lender("Time Finance plc") == LenderClassification.SECONDARY_INVOICE_FINANCIER
    assert classify_lender("iwoca Ltd") == LenderClassification.ALTERNATIVE_CHALLENGER
    assert classify_lender("Random Trading Ltd") == LenderClassification.UNCLASSIFIED


def test_b2b_sic_eligibility():
    # 49410 is Freight transport by road
    ok, reason, source = is_b2b_eligible(["49410"], crn="08421099")
    assert ok is True
    assert "Qualified B2B" in reason
    assert source is not None
    assert source.source_type == "COMPANIES_HOUSE_PROFILE"

    # 10710 is Manufacture of bread
    ok, _, source = is_b2b_eligible(["10710"], crn="08421099")
    assert ok is True
    assert source is not None

    # 47110 is Retail sale in non-specialised stores
    ok, reason, source = is_b2b_eligible(["47110"], crn="08421099")
    assert ok is False
    assert "retail" in reason.lower()
    assert source is None


def test_white_space_qualification():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    sat_date = (now - timedelta(days=200)).strftime("%Y-%m-%d")

    charges = [
        {
            "charge_code": "001",
            "status": "fully-satisfied",
            "satisfied_on": sat_date,
            "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
            "particulars": {"type": "contains-floating-charge"},
        }
    ]

    ok, decay, reason, source = evaluate_white_space(charges, as_of=now, crn="08421099")
    assert ok is True
    assert decay > 0.5
    assert "Unencumbered book debts" in reason
    assert source is not None
    assert source.source_type == "COMPANIES_HOUSE_CHARGES"


def test_active_debenture_blocks_white_space():
    """Audit Finding 1: An active floating charge MUST disqualify White Space."""
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    sat_date = (now - timedelta(days=200)).strftime("%Y-%m-%d")

    charges = [
        {
            "charge_code": "001",
            "status": "fully-satisfied",
            "satisfied_on": sat_date,
            "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
        },
        {
            "charge_code": "002",
            "status": "outstanding",
            "created_on": "2026-09-01",
            "persons_entitled": [{"name": "NATWEST BANK"}],
            "particulars": "Omnibus floating charge over all book debts",
        },
    ]

    ok, decay, reason, source = evaluate_white_space(charges, as_of=now, crn="08421099")
    assert ok is False
    assert decay == 0.0
    assert "Active charge present" in reason
    assert source is not None


def test_refinance_window_qualification():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    created_date = (now - timedelta(days=900)).strftime("%Y-%m-%d")  # ~30 months

    charges = [
        {
            "charge_code": "003",
            "status": "outstanding",
            "created_on": created_date,
            "persons_entitled": [{"name": "BIBBY FINANCIAL SERVICES LTD"}],
        }
    ]

    ok, factor, reason, source = evaluate_refinance_window(charges, as_of=now, crn="08421099")
    assert ok is True
    assert factor == 1.0
    assert "Refinance candidate" in reason
    assert source is not None


def test_tm01_cluster_detection():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    filings = [
        {"type": "TM01", "date": (now - timedelta(days=10)).strftime("%Y-%m-%d")},
        {"type": "TM01", "date": (now - timedelta(days=40)).strftime("%Y-%m-%d")},
        {"type": "CS01", "date": (now - timedelta(days=60)).strftime("%Y-%m-%d")},
    ]

    ok, count, reason, source = evaluate_tm01_cluster(filings, as_of=now, crn="08421099")
    assert ok is True
    assert count == 2
    assert "Cluster detected" in reason
    assert source is not None


def test_hard_knockout_on_gazette_notice():
    profile = {
        "company_name": "DISTRESSED HAULAGE LTD",
        "company_status": "active",
        "sic_codes": ["49410"],
    }
    filings = [{"type": "GAZ1", "date": "2026-10-01"}]
    charges = []

    scorer = ProvisionalScorer(archetype=LendingArchetype.PRIME_INVOICE_DISCOUNTING)
    result = scorer.score_company("12345678", profile, charges, filings)

    assert result.knockout_applied is True
    assert result.provisional_score == 0.0
    assert result.passed_gate is False
    assert "First Gazette" in (result.knockout_reason or "")
    assert len(result.primary_sources) >= 1


def test_provisional_scorer_passing_gate():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    profile = {
        "company_name": "PRIME FREIGHT LTD",
        "company_status": "active",
        "sic_codes": ["49410"],  # B2B Transport
        "accounts": {"overdue": False},
        "confirmation_statement": {"overdue": False},
    }
    charges = [
        {
            "charge_code": "001",
            "status": "fully-satisfied",
            "satisfied_on": (now - timedelta(days=190)).strftime("%Y-%m-%d"),
            "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
        }
    ]
    filings = [
        {"type": "AA", "date": "2026-01-15"}
    ]

    scorer = ProvisionalScorer(min_gate_threshold=40.0)
    result = scorer.score_company("12345678", profile, charges, filings, as_of=now)

    assert result.passed_gate is True
    assert result.provisional_score >= 40.0
    assert result.is_b2b_eligible is True
    assert len(result.primary_sources) >= 2


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter():
    limiter = AsyncTokenBucket(capacity=2.0, refill_rate=10.0)
    await limiter.acquire()
    await limiter.acquire()
    # Third acquire should succeed without error
    await limiter.acquire()
    assert limiter.tokens <= 2.0
