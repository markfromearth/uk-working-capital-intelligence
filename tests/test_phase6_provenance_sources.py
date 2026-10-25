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


@pytest.mark.asyncio
async def test_source_timeout_cannot_become_false_negative_or_fabricated_positive(monkeypatch):
    """
    Pilot-Entry Gate Section 4 Mandatory Check:
    Proves that when an external source times out (httpx.ReadTimeout):
    1. Adapter health transitions to DEGRADED with explicit TIMED_OUT status and zero fabricated signals.
    2. ApprovedToolRegistry.execute_tool fails closed with a timeout error, increments circuit breaker
       failures, and does NOT record an 'executed' negative finding in InvestigationDecision.
    3. WardenPipelineOrchestrator.process_company halts with status='SOURCE_TIMEOUT' rather than scoring
       an incomplete charges/filings register as a clean or unencumbered company.
    4. SignalDetectionAgent explicitly flags `has_timed_out_sources=True` and lists the timed-out source
       in `degraded_sources`.
    """
    import httpx
    from backend.app.agents.contracts import AgentRole, AgentTaskRequest
    from backend.app.agents.signal_detection import SignalDetectionAgent
    from backend.app.database import SessionLocal, init_db
    from backend.app.models.investigation import InvestigationDecision
    from backend.app.providers.companies_house_ch import CompaniesHouseCHClient
    from backend.app.providers.registry import provider_registry
    from backend.app.services.pipeline_orchestrator import WardenPipelineOrchestrator
    from backend.app.services.tool_registry import approved_tool_registry

    init_db()
    db = SessionLocal()
    try:
        def _timeout_get(*args, **kwargs):
            raise httpx.ReadTimeout("Simulated upstream provider timeout")

        monkeypatch.setattr(httpx, "get", _timeout_get)

        # 1. Direct adapter timeout check
        ch = provider_registry.get_adapter("companies_house")
        monkeypatch.setattr(ch, "_is_live", True)
        sigs = ch.fetch_company_signals("08931201")
        assert sigs == []
        assert ch.last_fetch_status == "TIMED_OUT"
        assert ch.get_health_status().status == AdapterHealthStatus.DEGRADED
        assert "TIMED_OUT" in ch.get_health_status().message

        # 2. ApprovedToolRegistry must NOT record a timeout as a successful 0-signal negative execution
        cb = approved_tool_registry.get_circuit_breaker("companies_house_charges_refresh")
        cb.reset()
        before_count = (
            db.query(InvestigationDecision)
            .filter(
                InvestigationDecision.crn == "08931201",
                InvestigationDecision.tool_name == "companies_house_charges_refresh",
                InvestigationDecision.status == "executed",
            )
            .count()
        )
        res = approved_tool_registry.execute_tool("companies_house_charges_refresh", "08931201", db)
        assert res["success"] is False
        assert "Source timeout" in res["error"]
        assert cb.consecutive_failures == 1
        after_count = (
            db.query(InvestigationDecision)
            .filter(
                InvestigationDecision.crn == "08931201",
                InvestigationDecision.tool_name == "companies_house_charges_refresh",
                InvestigationDecision.status == "executed",
            )
            .count()
        )
        assert after_count == before_count
        cb.reset()

        # 3. SignalDetectionAgent explicitly surfaces TIMED_OUT in degraded_sources
        sig_agent = SignalDetectionAgent()
        agent_res = await sig_agent.execute(
            AgentTaskRequest(target_role=AgentRole.SIGNAL_DETECTION, crn="08931201", budget_limit_gbp=0.0)
        )
        assert agent_res.output_payload["has_timed_out_sources"] is True
        assert any(
            d["provider"] == "companies_house" and d["status"] == "TIMED_OUT"
            for d in agent_res.output_payload["degraded_sources"]
        )

        # 4. WardenPipelineOrchestrator halts with SOURCE_TIMEOUT when charges fetch times out
        ch_client = CompaniesHouseCHClient(api_key="live_key_for_timeout_test")

        async def _ok_profile(crn: str):
            return {
                "company_number": crn,
                "company_name": "Timeout Guard Test Ltd",
                "company_status": "active",
                "type": "ltd",
                "sic_codes": ["49410"],
            }

        async def _timeout_charges(crn: str):
            ch_client.last_fetch_status = "TIMED_OUT"
            ch_client.last_error = "ReadTimeout on /charges"
            return []

        monkeypatch.setattr(ch_client, "fetch_profile", _ok_profile)
        monkeypatch.setattr(ch_client, "fetch_charges", _timeout_charges)
        orch = WardenPipelineOrchestrator(ch_client=ch_client)
        pipe_res = await orch.process_company("08931201")
        assert pipe_res["status"] == "SOURCE_TIMEOUT"
        assert pipe_res["screening"] is None
        assert pipe_res["dossier"] is None
    finally:
        ch_adapter = provider_registry.get_adapter("companies_house")
        ch_adapter.last_fetch_status = "IDLE"
        ch_adapter.last_error = None
        db.close()
