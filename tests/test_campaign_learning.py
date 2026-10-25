"""
Tests for Campaign Outcome Measurement, CSV Ingestion, Bayesian EVOI Learning, and Agent H.
Verifies deterministic campaign IDs, zero future data leakage, and empirical conversion updates.
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy.orm import Session

from backend.app.agents.campaign_measurement import CampaignMeasurementAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.database import Base, engine, SessionLocal
from backend.app.models.marketing import CampaignOutcomeEvent, CampaignTrackingRecord
from backend.app.services.outcome_learning import OutcomeLearningService


@pytest.fixture(scope="module", autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(CampaignOutcomeEvent).delete()
        db.query(CampaignTrackingRecord).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_issue_deterministic_campaign_tracking_code(db_session: Session):
    """Verify deterministic CMP-YYYYMM-XXXX tracking code generation and persistence."""
    service = OutcomeLearningService(db_session)
    record = service.issue_campaign_tracking_id(
        strategy_id="STRAT-CONTRACT-01",
        channel_name="Haulage & Logistics Today",
        channel_type="PUBLICATION",
        content_format="TRADE_PUBLICATION_ARTICLE",
        crn="08931201",
        budget_gbp=500.0,
    )

    assert record.id is not None
    assert record.campaign_id.startswith("CMP-")
    parts = record.campaign_id.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 6  # YYYYMM
    assert record.utm_campaign == "wcf_strat_contract_01"
    assert "haulage" in record.utm_source
    assert record.utm_medium == "publication"


def test_zero_future_leakage_in_empirical_metrics(db_session: Session):
    """Verify as_of timestamp filter strictly excludes future outcome events."""
    service = OutcomeLearningService(db_session)
    rec = service.issue_campaign_tracking_id(
        strategy_id="STRAT-LEAK-TEST",
        channel_name="Fleet Transport Podcast",
        channel_type="PODCAST",
        content_format="PODCAST_SPONSORSHIP",
    )
    cid = rec.campaign_id

    t_past = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_cutoff = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    t_future = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Event in past (qualified enquiry)
    service.record_campaign_outcome_event(
        campaign_id=cid,
        event_type="QUALIFIED_ENQUIRY",
        crn="08931201",
        event_timestamp=t_past,
    )

    # Event in future relative to cutoff (funded deal)
    service.record_campaign_outcome_event(
        campaign_id=cid,
        event_type="FUNDED_DEAL",
        crn="08931201",
        funded_amount_gbp=250_000.0,
        realized_commission_gbp=3_750.0,
        event_timestamp=t_future,
    )

    # Query strictly as_of cutoff
    past_metrics = service.compute_empirical_campaign_conversion_rates(campaign_id=cid, as_of=t_cutoff)
    assert past_metrics["qualified_enquiries"] == 1
    assert past_metrics["funded_deals"] == 0
    assert past_metrics["total_funded_volume_gbp"] == 0.0

    # Query full history (as_of None)
    all_metrics = service.compute_empirical_campaign_conversion_rates(campaign_id=cid)
    assert all_metrics["qualified_enquiries"] == 1
    assert all_metrics["funded_deals"] == 1
    assert all_metrics["total_funded_volume_gbp"] == 250_000.0


def test_csv_outcome_batch_ingestion(db_session: Session):
    """Verify CSV import parses rows and records impressions, clicks, leads, and funded deals."""
    service = OutcomeLearningService(db_session)
    rec = service.issue_campaign_tracking_id(
        strategy_id="STRAT-CSV-TEST",
        channel_name="UK Construction Digest",
        channel_type="PUBLICATION",
        content_format="DIRECT_EXECUTIVE_BRIEF",
    )
    cid = rec.campaign_id

    csv_data = f"""campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp
{cid},IMPRESSION,08931201,0,0,2026-02-01T10:00:00Z
{cid},CLICK,08931201,0,0,2026-02-01T10:05:00Z
{cid},QUALIFIED_ENQUIRY,08931201,0,0,2026-02-02T09:00:00Z
{cid},APPLICATION,08931201,0,0,2026-02-03T11:00:00Z
{cid},FUNDED_DEAL,08931201,150000.0,2250.0,2026-02-05T16:00:00Z
"""
    result = service.ingest_campaign_outcomes_csv(csv_data)
    assert result["ingested"] == 5
    assert result["errors"] == 0

    metrics = service.compute_empirical_campaign_conversion_rates(campaign_id=cid)
    assert metrics["impressions"] >= 1
    assert metrics["clicks"] >= 1
    assert metrics["qualified_enquiries"] >= 1
    assert metrics["applications"] >= 1
    assert metrics["funded_deals"] >= 1
    assert metrics["funding_approval_rate"] == 1.0


def test_bayesian_evoi_prior_updating(db_session: Session):
    """Verify Bayesian conjugate Beta-Binomial updating adjusts conversion priors smoothly."""
    service = OutcomeLearningService(db_session)
    update = service.update_bayesian_evoi_priors(
        channel_type="PODCAST",
        observed_successes=3,
        observed_trials=10,
        prior_alpha=2.0,
        prior_beta=20.0,
    )

    assert update["channel_type"] == "PODCAST"
    assert update["posterior_alpha"] == 5.0  # 2 + 3
    assert update["posterior_beta"] == 27.0  # 20 + (10 - 3)
    assert update["posterior_conversion_mean"] > update["prior_conversion_mean"]
    assert update["evoi_gain_multiplier"] > 1.0


@pytest.mark.asyncio
async def test_campaign_measurement_agent_execution(db_session: Session):
    """Verify Agent H executes cleanly through typed envelope contract."""
    agent = CampaignMeasurementAgent(db=db_session)
    assert agent.role == AgentRole.CAMPAIGN_MEASUREMENT

    req = AgentTaskRequest(
        target_role=AgentRole.CAMPAIGN_MEASUREMENT,
        crn="08931201",
        budget_limit_gbp=0.00,
        input_payload={
            "action": "ISSUE_CODE",
            "strategy_id": "STRAT-REFINANCE-02",
            "channel_name": "CFO Thought Leadership Forum",
            "channel_type": "COMMUNITY",
            "content_format": "EXPERT_ROUNDTABLE",
            "budget_gbp": 0.0,
        },
    )

    res = await agent.execute(req)
    assert res.status == AgentTaskStatus.COMPLETED
    assert res.cost_incurred_gbp == 0.00
    assert "campaign_id" in res.output_payload
    assert res.output_payload["campaign_id"].startswith("CMP-")
    assert len(res.evidence_references) >= 1
