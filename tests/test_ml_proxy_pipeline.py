import pytest
from backend.app.database import SessionLocal, init_db
from backend.app.config import settings
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.tool_registry import approved_tool_registry
from backend.app.ml.proxy_pipeline import CompaniesHouseProxyPipeline


@pytest.fixture(scope="module", autouse=True)
def setup_proxy_pipeline_env():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)
    # Ensure test clean baseline
    db.query(CommercialFunnelOutcome).delete()
    db.commit()
    db.close()


def test_proxy_pipeline_backfill_and_circuit_breaker(monkeypatch):
    """
    Verifies that the proxy pipeline:
    1. Operates on fixtures when DEMO_MODE=True.
    2. Respects circuit breaker trip/open state.
    3. Produces explicit metadata documenting that this measures finance demand, not broker conversion.
    4. Never writes to commercial_funnel_outcomes.
    """
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    db = SessionLocal()
    pipeline = CompaniesHouseProxyPipeline(db, registry=approved_tool_registry)

    # 1. Backfill against synthetic/demo CRNs
    crns = ["08931201", "SC123456"]
    res = pipeline.backfill_charges(crns=crns, min_interval_seconds=0.01)

    assert res["processed_crns"] == 2
    assert res["circuit_breaker_open"] is False
    assert "FINANCING_DEMAND_PROXY" in res["proxy_label_concept"]

    # 2. Verify circuit breaker trips on persistent failures
    cb = approved_tool_registry.get_circuit_breaker("companies_house_charges_refresh")
    cb.reset()

    # Artificially trip circuit breaker
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is True

    # Attempt backfill while circuit breaker is open
    blocked_res = pipeline.backfill_charges(crns=["08931201"], min_interval_seconds=0.01)
    assert blocked_res["processed_crns"] == 0
    assert any("Circuit breaker is OPEN" in err["error"] for err in blocked_res["errors"])

    # Reset circuit breaker
    cb.reset()
    assert cb.is_open is False

    # 3. Verify zero rows written to commercial_funnel_outcomes
    outcome_count = db.query(CommercialFunnelOutcome).count()
    assert outcome_count == 0, "Proxy pipeline must NEVER write sales funnel outcomes to application DB!"

    db.close()
