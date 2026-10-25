"""
Phase 6 Verification Suite:
Free-Source Operational Integrity, Provenance Metadata & Live-vs-Simulated Isolation.
"""

import json
import pytest
from backend.app.config import settings
from backend.app.providers.base import AdapterHealthStatus
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.providers.contracts_finder import ContractsFinderAdapter
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.experian import ExperianAdapter
from backend.app.providers.the_gazette import GazetteAdapter
from backend.app.providers.workforce import WorkforceAdapter


def test_simulated_signals_are_never_labeled_verified(monkeypatch):
    """
    Phase 6 Mandatory Negative Test:
    When running in simulation/DEMO_MODE without live API keys, providers MUST NOT
    label simulated signals with verification_status='verified'.
    """
    monkeypatch.setattr(settings, "DEMO_MODE", True)

    ch = CompaniesHouseAdapter(api_key="")
    assert ch._is_live is False
    # Pick a CRN that generates simulated charges (pattern 0 or 1)
    ch_signals = ch.fetch_company_signals("08931201")
    for s in ch_signals:
        assert s["verification_status"] == "simulated"
        prov = json.loads(s["provenance_details"])
        assert prov["mode"] == "SIMULATED"

    cs = CreditsafeAdapter(api_key="")
    for crn in ("08931201", "01234567", "09876543", "04321987"):
        for s in cs.fetch_company_signals(crn):
            assert s["verification_status"] == "simulated"
            prov = json.loads(s["provenance_details"])
            assert prov["mode"] == "SIMULATED"

    exp = ExperianAdapter(api_key="")
    for i in range(10):
        crn = f"0893120{i}"
        for s in exp.fetch_company_signals(crn):
            assert s["verification_status"] == "simulated"
            prov = json.loads(s["provenance_details"])
            assert prov["mode"] == "SIMULATED"

    wf = WorkforceAdapter(api_key="")
    wf_signals = wf.fetch_company_signals("08931201")
    assert len(wf_signals) >= 1
    for s in wf_signals:
        assert s["verification_status"] == "simulated"
        prov = json.loads(s["provenance_details"])
        assert prov["mode"] == "SIMULATED"


def test_unconfigured_providers_fail_closed_when_demo_mode_false(monkeypatch):
    """
    Verify that when DEMO_MODE=False and API keys are unconfigured, providers return
    UNCONFIGURED health status and zero fabricated signals.
    """
    monkeypatch.setattr(settings, "DEMO_MODE", False)

    for adapter in (
        CompaniesHouseAdapter(api_key=""),
        CreditsafeAdapter(api_key=""),
        ExperianAdapter(api_key=""),
        WorkforceAdapter(api_key=""),
    ):
        health = adapter.get_health_status()
        assert health.status == AdapterHealthStatus.UNCONFIGURED
        assert adapter.fetch_company_signals("08931201") == []


def test_fixture_vs_live_provenance_in_contracts_finder_and_gazette():
    """
    Verify ContractsFinderAdapter and GazetteAdapter distinguish FIXTURE from LIVE records.
    """
    cf = ContractsFinderAdapter()
    cf.register_fixture(
        "08931201",
        [
            {
                "notice_id": "CF-FIX-01",
                "award": {"title": "Fixture Contract", "buyer": "DfT", "value": 200000.0},
            },
            {
                "notice_id": "CF-LIVE-01",
                "_live": True,
                "award": {"title": "Live Contract", "buyer": "NHS", "value": 500000.0},
            },
        ],
    )
    cf_signals = cf.fetch_company_signals("08931201")
    assert len(cf_signals) == 2
    assert cf_signals[0]["verification_status"] == "fixture"
    assert json.loads(cf_signals[0]["provenance_details"])["mode"] == "FIXTURE"
    assert cf_signals[1]["verification_status"] == "verified"
    assert json.loads(cf_signals[1]["provenance_details"])["mode"] == "LIVE"

    gz = GazetteAdapter()
    gz.register_fixture(
        "08931201",
        [
            {"notice_id": "GZ-FIX-01", "notice_type": "Fixture Notice"},
            {"notice_id": "GZ-LIVE-01", "_live": True, "notice_type": "Live Notice"},
        ],
    )
    gz_signals = gz.fetch_company_signals("08931201")
    assert len(gz_signals) == 2
    assert gz_signals[0]["verification_status"] == "fixture"
    assert json.loads(gz_signals[0]["provenance_details"])["mode"] == "FIXTURE"
    assert gz_signals[1]["verification_status"] == "verified"
    assert json.loads(gz_signals[1]["provenance_details"])["mode"] == "LIVE"
