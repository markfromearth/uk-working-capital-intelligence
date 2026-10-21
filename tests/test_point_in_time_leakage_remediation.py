import uuid
import pytest
from datetime import datetime, timezone, timedelta
from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry
from backend.app.models.signal import NormalizedSignal
from backend.app.services.feature_store import FeatureStoreService

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    crn = "07777111"
    comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first()
    if not comp:
        comp = CompanyRegistry(crn=crn, company_name="Leakage Audit Testing Ltd")
        db.add(comp)
        db.commit()
    yield db
    db.close()

def test_late_arriving_event_leakage_prevention(db_session):
    """
    Tests that an event with an old observation date that was INGESTED after cutoff
    is strictly excluded from a historical snapshot.
    """
    crn = "07777111"
    # Clean previous signals for CRN
    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    fs = FeatureStoreService(db_session)
    cutoff = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Event observed in April 2026, but only ingested into the system in August 2026 (after cutoff)
    late_arriving_sig = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        observation_timestamp=datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc),
        ingestion_timestamp=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        value_numeric=25.0,
        value_text="25 days DBT",
        direction="strain_increasing",
        verification_status="verified"
    )
    db_session.add(late_arriving_sig)
    db_session.commit()

    # Historical snapshot as of June 1, 2026 must NOT see late_arriving_sig
    snap_as_of_june = fs.compute_feature_snapshot(crn, as_of_timestamp=cutoff)
    assert snap_as_of_june.dbt_current_days is None
    assert snap_as_of_june.dbt_data_available == 0

    # Snapshot as of September 2026 sees it
    snap_as_of_sept = fs.compute_feature_snapshot(crn, as_of_timestamp=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert snap_as_of_sept.dbt_current_days == 25.0
    assert snap_as_of_sept.dbt_data_available == 1

def test_disputed_and_superseded_signals_excluded(db_session):
    crn = "07777111"
    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    fs = FeatureStoreService(db_session)
    now = datetime.now(timezone.utc)

    # Signal marked disputed by broker
    disputed_sig = NormalizedSignal(
        crn=crn,
        signal_type="charge_satisfaction_mr04",
        source_provider="companies_house",
        observation_timestamp=now - timedelta(days=10),
        ingestion_timestamp=now - timedelta(days=10),
        value_numeric=1.0,
        value_text="Disputed charge satisfaction",
        direction="strain_increasing",
        verification_status="disputed"
    )
    # Signal marked superseded
    superseded_sig = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        observation_timestamp=now - timedelta(days=15),
        ingestion_timestamp=now - timedelta(days=15),
        value_numeric=35.0,
        direction="strain_increasing",
        verification_status="superseded"
    )
    db_session.add(disputed_sig)
    db_session.add(superseded_sig)
    db_session.commit()

    snap = fs.compute_feature_snapshot(crn, as_of_timestamp=now)
    assert snap.charges_satisfied_90d == 0
    assert snap.dbt_current_days is None
    assert snap.corroboration_signal_count == 0
