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
    # Create test company
    crn = "09999888"
    comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first()
    if not comp:
        comp = CompanyRegistry(crn=crn, company_name="Time Travel Test Ltd", created_at=datetime.now(timezone.utc))
        db.add(comp)
        db.commit()
    yield db
    db.close()

def test_point_in_time_historical_isolation(db_session):
    crn = "09999888"
    # Clean previous signals for test isolation
    db_session.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).delete()
    db_session.commit()

    fs = FeatureStoreService(db_session)
    t_past = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_future = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Signal 1: Occurred and ingested before t_past
    sig1 = NormalizedSignal(
        crn=crn,
        signal_type="dbt_increase",
        source_provider="creditsafe",
        observation_timestamp=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        ingestion_timestamp=datetime(2026, 5, 15, 12, 5, 0, tzinfo=timezone.utc),
        value_numeric=15.0,
        value_text="DBT is 15 days",
        direction="strain_increasing"
    )
    # Signal 2: Occurred and ingested AFTER t_past (in July 2026)
    sig2 = NormalizedSignal(
        crn=crn,
        signal_type="charge_satisfaction_mr04",
        source_provider="companies_house",
        observation_timestamp=datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc),
        ingestion_timestamp=datetime(2026, 7, 10, 12, 5, 0, tzinfo=timezone.utc),
        value_numeric=1.0,
        value_text="Charge satisfied MR04",
        direction="strain_increasing"
    )
    db_session.add(sig1)
    db_session.add(sig2)
    db_session.commit()

    # 1. Historical Snapshot as of June 1, 2026
    past_snap = fs.compute_feature_snapshot(crn, as_of_timestamp=t_past)
    # Must NOT include sig2 (satisfaction in July)
    assert past_snap.charges_satisfied_90d == 0
    assert past_snap.dbt_current_days == 15.0
    assert past_snap.dso_data_available == 0  # Missing DSO is explicitly noted

    # 2. Modern Snapshot as of September 1, 2026
    future_snap = fs.compute_feature_snapshot(crn, as_of_timestamp=t_future)
    # Now sig2 is within the 90d window
    assert future_snap.charges_satisfied_90d == 1
    assert future_snap.dbt_current_days == 15.0
