"""
Phase 7 Verification Suite:
Campaign Economics, Zero-Cost ROI Handling, Bayesian EVOI Bounds & Outcome Measurement.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from backend.app.database import Base, engine, SessionLocal
from backend.app.models.marketing import CampaignOutcomeEvent, CampaignTrackingRecord
from backend.app.schemas.marketing import ScenarioType
from backend.app.services.campaign_economics import CampaignEconomicsService
from backend.app.services.outcome_learning import OutcomeLearningService


@pytest.fixture(scope="module", autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_zero_cost_campaign_does_not_return_magic_999_roi():
    """
    Phase 7 Mandatory Negative Test:
    Zero-cost campaigns must return roi_multiple=None and roi_status='not_applicable_zero_cost',
    never a magic 999.0 sentinel.
    """
    model = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=350_000.0,
        campaign_cost_gbp=0.0,
        target_reach=1000,
    )
    assert model.evidence_basis == "ILLUSTRATIVE_PRIOR_BENCHMARK"
    for stype in (ScenarioType.CONSERVATIVE.value, ScenarioType.BASE.value, ScenarioType.OPTIMISTIC.value):
        sc = model.scenarios[stype]
        assert sc.roi_multiple is None
        assert sc.roi_status == "not_applicable_zero_cost"


def test_bayesian_evoi_rejects_impossible_trial_counts(db_session: Session):
    """
    Phase 7 Mandatory Negative Test:
    observed_successes > observed_trials, negative counts, or non-positive priors must raise ValueError.
    """
    service = OutcomeLearningService(db_session)

    with pytest.raises(ValueError, match="cannot exceed observed_trials"):
        service.update_bayesian_evoi_priors(
            channel_type="PODCAST",
            observed_successes=11,
            observed_trials=10,
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        service.update_bayesian_evoi_priors(
            channel_type="PODCAST",
            observed_successes=-1,
            observed_trials=10,
        )

    with pytest.raises(ValueError, match="strictly positive"):
        service.update_bayesian_evoi_priors(
            channel_type="PODCAST",
            observed_successes=2,
            observed_trials=10,
            prior_alpha=0.0,
        )


def test_bayesian_evoi_includes_credible_intervals_and_version(db_session: Session):
    """
    Verify Bayesian conjugate Beta-Binomial update includes 95% credible intervals,
    posterior standard deviation, model version, and cutoff timestamp.
    """
    service = OutcomeLearningService(db_session)
    cutoff = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    res = service.update_bayesian_evoi_priors(
        channel_type="PUBLICATION",
        observed_successes=4,
        observed_trials=20,
        prior_alpha=2.0,
        prior_beta=18.0,
        as_of=cutoff,
    )
    assert "credible_interval_95" in res
    low, high = res["credible_interval_95"]
    assert 0.0 <= low < res["posterior_conversion_mean"] < high <= 1.0
    assert res["posterior_std"] > 0.0
    assert res["model_version"] == "beta-binomial-v2.0"
    assert res["cutoff_as_of"] == "2026-06-01T12:00:00+00:00"


def test_duplicate_csv_imports_do_not_double_count_revenue(db_session: Session):
    """
    Phase 7 Mandatory Negative Test:
    Re-importing the identical CSV outcome batch must deduplicate events via SHA-256
    event_fingerprint and must not double-count funded deals or commission revenue.
    """
    service = OutcomeLearningService(db_session)
    rec = service.issue_campaign_tracking_id(
        strategy_id="STRAT-DEDUP-PHASE7",
        channel_name="Trade Finance Weekly",
        channel_type="PUBLICATION",
        content_format="TRADE_PUBLICATION_ARTICLE",
        crn="08931201",
    )
    cid = rec.campaign_id
    db_session.query(CampaignOutcomeEvent).filter(CampaignOutcomeEvent.campaign_id == cid).delete()
    db_session.commit()

    csv_payload = f"""campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp
{cid},QUALIFIED_ENQUIRY,08931201,0,0,2026-03-01T10:00:00Z
{cid},APPLICATION,08931201,0,0,2026-03-02T10:00:00Z
{cid},FUNDED_DEAL,08931201,200000.0,3000.0,2026-03-05T14:00:00Z
"""
    first_import = service.ingest_campaign_outcomes_csv(csv_payload)
    assert first_import["ingested"] == 3
    assert first_import["duplicates"] == 0

    second_import = service.ingest_campaign_outcomes_csv(csv_payload)
    assert second_import["ingested"] == 0
    assert second_import["duplicates"] == 3

    metrics = service.compute_empirical_campaign_conversion_rates(campaign_id=cid)
    assert metrics["funded_deals"] == 1
    assert metrics["total_funded_volume_gbp"] == 200_000.0
    assert metrics["total_realized_commission_gbp"] == 3_000.0


def test_negative_monetary_outcome_amounts_rejected(db_session: Session):
    """
    Phase 7 Mandatory Negative Test:
    Negative funded_amount_gbp or realized_commission_gbp must be rejected.
    """
    service = OutcomeLearningService(db_session)
    rec = service.issue_campaign_tracking_id(
        strategy_id="STRAT-NEG-AMT",
        channel_name="B2B Finance Briefing",
        channel_type="PUBLICATION",
        content_format="DIRECT_EXECUTIVE_BRIEF",
        crn="08931201",
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        service.record_campaign_outcome_event(
            campaign_id=rec.campaign_id,
            event_type="FUNDED_DEAL",
            crn="08931201",
            funded_amount_gbp=-50_000.0,
            realized_commission_gbp=500.0,
        )
