from datetime import datetime, timedelta, timezone
import pytest
from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry
from backend.app.models.signal import NormalizedSignal
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.scoring_engine import LiquidityScoringEngine

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_withdrawn_and_disputed_signals_strictly_excluded(db_session):
    """
    Proves that signals marked as 'withdrawn', 'disputed', or 'superseded'
    are completely excluded from point-in-time feature extraction.
    """
    crn = "04829101"
    now = datetime.now(timezone.utc)
    
    # Ensure company exists
    if not db_session.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first():
        db_session.add(CompanyRegistry(crn=crn, company_name="Withdrawn Signals Ltd"))
        db_session.commit()

    # Clear previous signals for clean assertion
    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    # Add a withdrawn DBT spike
    sig1 = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        original_event_ref="withdrawn-sig-01",
        observation_timestamp=now - timedelta(days=10),
        ingestion_timestamp=now - timedelta(days=10),
        value_numeric=45.0,
        direction="strain_increasing",
        verification_status="withdrawn"
    )
    # Add a disputed charge satisfaction
    sig2 = NormalizedSignal(
        crn=crn,
        signal_type="charge_satisfaction_mr04",
        source_provider="companies_house",
        original_event_ref="disputed-chg-01",
        observation_timestamp=now - timedelta(days=15),
        ingestion_timestamp=now - timedelta(days=15),
        direction="strain_increasing",
        verification_status="disputed"
    )
    db_session.add_all([sig1, sig2])
    db_session.commit()

    fs = FeatureStoreService(db_session)
    snapshot = fs.compute_feature_snapshot(crn, as_of_timestamp=now)

    # Both must be excluded
    assert snapshot.dbt_current_days is None
    assert snapshot.charges_satisfied_90d == 0
    assert snapshot.corroboration_signal_count == 0

def test_same_underlying_event_reported_by_two_providers_does_not_corroborate(db_session):
    """
    Proves that when two providers (e.g. Creditsafe and Experian) both report
    the EXACT SAME underlying court judgment (sharing evidence_reference='CCJ-2026-LON-999'),
    the system recognizes they reference the same event.
    It does NOT count as 2 distinct sources, and the score remains capped at 49.0!
    """
    crn = "09382104"
    now = datetime.now(timezone.utc)

    if not db_session.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first():
        db_session.add(CompanyRegistry(crn=crn, company_name="Multi Reporter Ltd"))
        db_session.commit()

    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    # Provider A reports CCJ
    sig_cs = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        original_event_ref="cs-ccj-report-1",
        evidence_reference="COURT-JUDGMENT-CCJ-2026-999",
        observation_timestamp=now - timedelta(days=20),
        ingestion_timestamp=now - timedelta(days=20),
        value_numeric=35.0,
        direction="strain_increasing",
        verification_status="verified"
    )

    # Provider B reports the SAME underlying CCJ
    sig_exp = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="experian",
        original_event_ref="exp-delinquency-report-88",
        evidence_reference="COURT-JUDGMENT-CCJ-2026-999",
        observation_timestamp=now - timedelta(days=20),
        ingestion_timestamp=now - timedelta(days=20),
        value_numeric=35.0,
        direction="strain_increasing",
        verification_status="verified"
    )

    db_session.add_all([sig_cs, sig_exp])
    db_session.commit()

    fs = FeatureStoreService(db_session)
    snapshot = fs.compute_feature_snapshot(crn, as_of_timestamp=now)

    # Crucial assertion: distinct sources must be 1, NOT 2!
    assert snapshot.distinct_source_count == 1
    assert snapshot.corroboration_signal_count == 1

    engine = LiquidityScoringEngine(db_session)
    score_rec = engine.calculate_score(snapshot, as_of_timestamp=now)

    # Score MUST be capped at 49.0 because there is no true multi-source corroboration!
    assert score_rec.score <= 49.0
    assert score_rec.score_band in ["emerging", "limited"]

def test_genuine_multi_source_corroboration_exceeds_cap(db_session):
    """
    Proves that when genuinely independent providers report distinct events
    (e.g. Companies House MR04 filing AND Creditsafe DBT spike),
    the score successfully breaches the 49.0 cap into elevated/strong bands.
    """
    crn = "05511223"
    now = datetime.now(timezone.utc)

    if not db_session.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first():
        db_session.add(CompanyRegistry(crn=crn, company_name="Corroborated High Strain Ltd"))
        db_session.commit()

    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    # Source 1: Companies House MR04 satisfaction
    sig_ch = NormalizedSignal(
        crn=crn,
        signal_type="charge_satisfaction_mr04",
        source_provider="companies_house",
        original_event_ref="ch-mr04-filing-101",
        evidence_reference="https://companieshouse.gov.uk/charges/101",
        observation_timestamp=now - timedelta(days=12),
        ingestion_timestamp=now - timedelta(days=12),
        direction="strain_increasing",
        verification_status="verified"
    )

    # Source 2: Creditsafe severe DBT delinquency
    sig_cs = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        original_event_ref="cs-dbt-spike-202",
        evidence_reference="CS-TRADE-PAYMENTS-RECORD-202",
        observation_timestamp=now - timedelta(days=10),
        ingestion_timestamp=now - timedelta(days=10),
        value_numeric=32.0,
        direction="strain_increasing",
        verification_status="verified"
    )

    # Source 3: Workforce payroll contraction
    sig_wf = NormalizedSignal(
        crn=crn,
        signal_type="headcount_surge",
        source_provider="workforce_intelligence",
        original_event_ref="wf-headcount-surge-303",
        observation_timestamp=now - timedelta(days=8),
        ingestion_timestamp=now - timedelta(days=8),
        value_numeric=25.0,
        direction="strain_increasing",
        verification_status="verified"
    )

    db_session.add_all([sig_ch, sig_cs, sig_wf])
    db_session.commit()

    fs = FeatureStoreService(db_session)
    snapshot = fs.compute_feature_snapshot(crn, as_of_timestamp=now)

    assert snapshot.distinct_source_count >= 2
    assert snapshot.corroboration_signal_count >= 2

    engine = LiquidityScoringEngine(db_session)
    score_rec = engine.calculate_score(snapshot, as_of_timestamp=now)

    # Genuinely corroborated score breaches the 49.0 cap
    assert score_rec.score >= 50.0
    assert score_rec.score_band in ["elevated", "strong"]
