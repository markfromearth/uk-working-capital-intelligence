import uuid
import pytest
from datetime import datetime, timezone
from backend.app.database import SessionLocal, init_db
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.company import CompanyRegistry
from backend.app.services.scoring_engine import LiquidityScoringEngine

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    # Ensure test company exists
    comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == "08931201").first()
    if not comp:
        comp = CompanyRegistry(crn="08931201", company_name="Apex Precision Test Ltd")
        db.add(comp)
        db.commit()
    yield db
    db.close()

def test_single_source_uncorroborated_score_capped(db_session):
    engine = LiquidityScoringEngine(db_session)
    now = datetime.now(timezone.utc)

    # Company with only 1 source (charge satisfaction only, no credit or headcount signals)
    snap = CompanyFeatureSnapshot(
        snapshot_id=str(uuid.uuid4()),
        crn="08931201",
        as_of_timestamp=now,
        charges_satisfied_30d=1,
        charges_satisfied_90d=1,
        charges_satisfied_180d=1,
        charges_created_90d=0,
        age_latest_charge_satisfaction_days=10.0,
        latest_charge_scope="full",
        corroboration_signal_count=1,
        distinct_source_count=1,
        evidence_coverage="emerging"
    )
    db_session.add(snap)
    db_session.commit()

    score_rec = engine.calculate_score(snap, now)
    
    # Must be capped at 49.0 ("emerging") because it lacks multi-source corroboration
    assert score_rec.score <= 49.0
    assert score_rec.score_band in ("emerging", "limited")
    assert score_rec.corroboration_score <= 4.0

def test_multi_source_corroborated_high_strain_score(db_session):
    engine = LiquidityScoringEngine(db_session)
    now = datetime.now(timezone.utc)

    # Company with all 3 pillars triggered and 3 distinct sources
    snap = CompanyFeatureSnapshot(
        snapshot_id=str(uuid.uuid4()),
        crn="08931201",
        as_of_timestamp=now,
        charges_satisfied_30d=1,
        charges_satisfied_90d=1,
        charges_satisfied_180d=1,
        charges_created_90d=0,
        age_latest_charge_satisfaction_days=12.0,
        latest_charge_scope="full",
        dbt_current_days=30.0,       # Severe payment lateness
        dbt_trend_90d=8.0,
        credit_score_drop_90d=25.0,  # Serious rating downgrade
        headcount_growth_90d_pct=40.0,
        headcount_acceleration_pct=15.0,
        headcount_confidence_score=0.9,
        corroboration_signal_count=4,
        distinct_source_count=3,
        evidence_coverage="strong"
    )
    db_session.add(snap)
    db_session.commit()

    score_rec = engine.calculate_score(snap, now)

    # Score should be elevated/strong (>= 75)
    assert score_rec.score >= 75.0
    assert score_rec.score_band == "strong"
    assert score_rec.corroboration_score == 20.0
    assert score_rec.financing_position_score > 0
    assert score_rec.credit_payment_risk_score > 0
    assert score_rec.workforce_expansion_score > 0

def test_contradictory_evidence_mitigation(db_session):
    engine = LiquidityScoringEngine(db_session)
    now = datetime.now(timezone.utc)

    # Company with contradictory positive indicators (e.g. debt repayment without strain)
    snap = CompanyFeatureSnapshot(
        snapshot_id=str(uuid.uuid4()),
        crn="08931201",
        as_of_timestamp=now,
        charges_satisfied_90d=1,
        charges_satisfied_180d=1,
        charges_created_90d=0,
        age_latest_charge_satisfaction_days=15.0,
        latest_charge_scope="full",
        dbt_current_days=2.0,  # Paying suppliers early
        corroboration_signal_count=1,
        distinct_source_count=1,
        evidence_coverage="emerging",
        contradictory_evidence_flags='["Prompt payment discount applied", "Recent equity funding"]'
    )
    db_session.add(snap)
    db_session.commit()

    score_rec = engine.calculate_score(snap, now)
    assert score_rec.contradictory_penalty > 0
    assert score_rec.score < 30.0
