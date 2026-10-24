"""
Test Suite for Contracts Finder (OCDS) & The Gazette Insolvency Public Data Adapters.
Verifies signal normalization, primary source attribution, indicator evaluation,
hard knockouts, and multi-product matching.
"""

from datetime import datetime, timezone, timedelta
import pytest

from backend.app.providers.base import AdapterHealthStatus
from backend.app.providers.contracts_finder import ContractsFinderAdapter
from backend.app.providers.the_gazette import GazetteAdapter
from backend.app.providers.registry import provider_registry
from backend.app.services.indicator_engine import (
    evaluate_contract_awards,
    evaluate_gazette_insolvency,
)
from backend.app.services.provisional_scorer import ProvisionalScorer
from backend.app.services.product_matcher import match_products
from backend.app.schemas.warden import LendingArchetype


def test_contracts_finder_adapter_health_and_normalization():
    adapter = ContractsFinderAdapter()
    status = adapter.get_health_status()
    assert status.status == AdapterHealthStatus.LIVE
    assert "OCDS" in status.message

    crn = "01234567"
    fixture = [
        {
            "notice_id": "ocds-b5fd17-1001",
            "uri": "https://www.contractsfinder.service.gov.uk/notice/ocds-b5fd17-1001",
            "award": {
                "title": "Highways Maintenance Framework Call-off",
                "buyer": "Department for Transport",
                "value": 1_250_000.0,
                "date": "2026-08-15",
            },
        }
    ]
    adapter.register_fixture(crn, fixture)
    signals = adapter.fetch_company_signals(crn)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["signal_type"] == "contract_award_growth"
    assert sig["source_provider"] == "contracts_finder"
    assert sig["value_numeric"] == 1_250_000.0
    assert sig["value_units"] == "GBP"
    assert "Department for Transport" in sig["value_text"]
    assert sig["confidence_score"] == 0.95
    assert sig["data_use_restrictions"] == "open_government_licence_v3"
    assert "contractsfinder.service.gov.uk" in sig["evidence_reference"]


def test_the_gazette_adapter_health_and_normalization():
    adapter = GazetteAdapter()
    status = adapter.get_health_status()
    assert status.status == AdapterHealthStatus.LIVE
    assert "Gazette" in status.message

    crn = "09876543"
    fixture = [
        {
            "notice_id": "GZ-4001",
            "notice_code": "2450",
            "notice_type": "Petitions to wind up (companies)",
            "title": "In the High Court of Justice, Business and Property Courts",
            "publication_date": "2026-09-01",
            "uri": "https://www.thegazette.co.uk/notice/GZ-4001",
        }
    ]
    adapter.register_fixture(crn, fixture)
    signals = adapter.fetch_company_signals(crn)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["signal_type"] == "gazette_insolvency_notice"
    assert sig["source_provider"] == "the_gazette"
    assert sig["direction"] == "strain_increasing"
    assert sig["confidence_score"] == 1.0
    assert "thegazette.co.uk/notice/GZ-4001" in sig["evidence_reference"]


def test_indicator_evaluate_contract_awards():
    now = datetime.now(timezone.utc)
    signals = [
        {
            "signal_type": "contract_award_growth",
            "observation_timestamp": now - timedelta(days=30),
            "value_numeric": 500_000.0,
            "value_text": "Won: MOD Logistics Support",
            "original_event_ref": "CF-99",
            "evidence_reference": "https://www.contractsfinder.service.gov.uk/notice/CF-99",
        }
    ]

    triggered, mob_cap, desc, source = evaluate_contract_awards(signals, as_of=now, crn="01234567")
    assert triggered is True
    assert mob_cap == 100_000.0  # 20% of £500,000
    assert "£100,000" in desc
    assert source is not None
    assert source.source_type == "CONTRACTS_FINDER"
    assert "CF-99" in source.uri


def test_indicator_evaluate_gazette_insolvency():
    now = datetime.now(timezone.utc)
    signals = [
        {
            "signal_type": "gazette_insolvency_notice",
            "observation_timestamp": now - timedelta(days=14),
            "value_text": "Appointment of Administrators",
            "original_event_ref": "GZ-777",
            "evidence_reference": "https://www.thegazette.co.uk/notice/GZ-777",
        }
    ]

    triggered, desc, source = evaluate_gazette_insolvency(signals, as_of=now, crn="09876543")
    assert triggered is True
    assert "Appointment of Administrators" in desc
    assert source is not None
    assert source.source_type == "GAZETTE_NOTICE"
    assert source.uri == "https://www.thegazette.co.uk/notice/GZ-777"


def test_provisional_scorer_gazette_knockout():
    """Verify that a Gazette corporate insolvency notice forces an immediate knockout."""
    crn = "09876543"
    profile = {
        "company_name": "DISTRESSED TRADING LTD",
        "company_status": "active",
        "sic_codes": ["49410"],
    }
    charges = []
    filings = []
    signals = [
        {
            "signal_type": "gazette_insolvency_notice",
            "observation_timestamp": datetime.now(timezone.utc) - timedelta(days=5),
            "value_text": "Winding-up Petition",
            "original_event_ref": "GZ-999",
            "evidence_reference": "https://www.thegazette.co.uk/notice/GZ-999",
        }
    ]

    scorer = ProvisionalScorer(archetype=LendingArchetype.PRIME_INVOICE_DISCOUNTING)
    result = scorer.score_company(crn, profile, charges, filings, signals=signals)

    assert result.knockout_applied is True
    assert result.passed_gate is False
    assert result.provisional_score == 0.0
    assert "The Gazette" in result.knockout_reason
    assert any(ps.source_type == "GAZETTE_NOTICE" for ps in result.primary_sources)


def test_provisional_scorer_contract_award_points_and_product_matching():
    """Verify contract award points increase score and match CONTRACT_FINANCE product."""
    crn = "01234567"
    profile = {
        "company_name": "EXPEDIENT LOGISTICS LTD",
        "company_status": "active",
        "sic_codes": ["49410"],  # B2B Freight
    }
    charges = []
    filings = []
    signals = [
        {
            "signal_type": "contract_award_growth",
            "observation_timestamp": datetime.now(timezone.utc) - timedelta(days=20),
            "value_numeric": 2_000_000.0,
            "value_text": "Won: NHS Supply Fleet",
            "original_event_ref": "CF-888",
            "evidence_reference": "https://www.contractsfinder.service.gov.uk/notice/CF-888",
        }
    ]

    scorer = ProvisionalScorer()
    result = scorer.score_company(crn, profile, charges, filings, signals=signals)

    # Awarded points for B2B sector fit + contract_award_growth
    assert result.knockout_applied is False
    assert result.provisional_score > 0.0
    ca_breakdown = [b for b in result.audit_trail if b.indicator == "contract_award_growth"]
    assert len(ca_breakdown) == 1
    assert ca_breakdown[0].triggered is True
    assert ca_breakdown[0].awarded > 0.0
    assert any(ps.source_type == "CONTRACTS_FINDER" for ps in result.primary_sources)

    # Multi-product matching check
    matched = match_products(result)
    product_codes = [p["code"] for p in matched]
    assert "CONTRACT_FINANCE" in product_codes
    assert "GROWTH_FINANCE" in product_codes
