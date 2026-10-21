import pytest
from datetime import datetime, timezone, timedelta

from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry
from backend.app.models.signal import NormalizedSignal
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.feature_store import FeatureStoreService
from backend.app.ml.features import (
    FEATURE_NAMES,
    extract_features_dict,
    extract_features_vector,
    get_feature_spec_hash
)
from backend.app.ml.dataset import (
    DatasetBuilder,
    TARGET_BROKER_FUNDED,
    TARGET_NEW_CHARGE_12M
)


@pytest.fixture(scope="module", autouse=True)
def setup_dataset_test_env():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)
    # Ensure test clean baseline
    db.query(CommercialFunnelOutcome).delete()
    db.commit()
    db.close()


def test_leakage_future_signals_strictly_isolated():
    """
    LEAKAGE TEST:
    Verifies that shifting or injecting a signal after snapshot_date
    cannot change that snapshot's features.
    """
    db = SessionLocal()
    fs = FeatureStoreService(db)
    crn = "08931201"

    # Base snapshot point: 100 days ago
    t_snapshot = datetime.now(timezone.utc) - timedelta(days=100)
    snap_before = fs.compute_feature_snapshot(crn, as_of_timestamp=t_snapshot)
    vec_before = extract_features_vector(snap_before)

    # Inject a future signal at t_snapshot + 10 days (which is 90 days ago)
    future_time = t_snapshot + timedelta(days=10)
    future_sig = NormalizedSignal(
        crn=crn,
        signal_type="charge_satisfaction_mr04",
        source_provider="companies_house",
        original_event_ref="leakage-test-future-chg-001",
        observation_timestamp=future_time,
        ingestion_timestamp=future_time,
        value_numeric=1.0,
        value_text="Debenture released fully (FUTURE EVENT)",
        value_units="scope_weight",
        direction="strain_increasing",
        confidence_score=0.99,
        verification_status="verified"
    )
    db.add(future_sig)
    db.commit()

    try:
        # Re-compute snapshot at EXACTLY t_snapshot
        snap_after = fs.compute_feature_snapshot(crn, as_of_timestamp=t_snapshot)
        vec_after = extract_features_vector(snap_after)

        # STRICT PROOF: Vector as-of t_snapshot must be completely unchanged by future event
        import math
        assert len(vec_before) == len(vec_after)
        for name, a, b in zip(FEATURE_NAMES, vec_before, vec_after):
            assert (a == b) or (math.isnan(a) and math.isnan(b)), f"Feature '{name}' leaked! before={a}, after={b}"

        # But a snapshot evaluated AFTER the future signal (e.g. t_snapshot + 15 days) DOES reflect it
        snap_later = fs.compute_feature_snapshot(crn, as_of_timestamp=t_snapshot + timedelta(days=15))
        assert snap_later.charges_satisfied_90d >= (snap_before.charges_satisfied_90d or 0) + 1

    finally:
        # Clean up test signal
        db.delete(future_sig)
        db.commit()
        db.close()


def test_right_censoring_drops_unelapsed_horizons():
    """
    Verifies that snapshots whose horizon has not fully elapsed are dropped
    as right-censored, and that the dropped count is accurately reported.
    """
    db = SessionLocal()
    builder = DatasetBuilder(db)
    crn = "08931201"
    now = datetime.now(timezone.utc)
    horizon_days = 180

    # Snapshot A: 60 days ago. Horizon ends in (now - 60d + 180d) = now + 120d -> UNELAPSED (CENSORED)
    snap_censored = now - timedelta(days=60)

    # Snapshot B: 240 days ago. Horizon ends in (now - 240d + 180d) = now - 60d -> FULLY ELAPSED
    snap_valid = now - timedelta(days=240)

    snapshot_dates = {
        crn: [snap_censored, snap_valid]
    }

    result = builder.build_dataset(
        crns=[crn],
        snapshot_dates=snapshot_dates,
        target=TARGET_BROKER_FUNDED,
        horizon_days=horizon_days,
        as_of_reference=now
    )

    # Exactly 2 candidate snapshots examined
    assert result.total_snapshots_examined == 2
    # Exactly 1 dropped due to right-censoring
    assert result.censored_dropped_count == 1
    # Exactly 1 survived into final dataset
    assert result.sample_count == 1
    assert result.records[0].snapshot_date.date() == snap_valid.date()

    db.close()


def test_funnel_stages_collapse_into_single_observation_row():
    """
    Verifies that multiple funnel stage events for a single company within the horizon
    collapse into exactly ONE labelled observation row with label=1 (not multiple duplicate rows).
    """
    db = SessionLocal()
    builder = DatasetBuilder(db)
    crn = "08931201"
    now = datetime.now(timezone.utc)
    horizon_days = 180

    # Snapshot: 250 days ago (fully elapsed horizon: ends 70 days ago)
    t_snapshot = now - timedelta(days=250)

    # Insert multiple progression stages in test DB strictly inside (t_snapshot, t_snapshot + 180d]
    o1 = CommercialFunnelOutcome(
        crn=crn,
        outcome_stage="QUALIFIED_CONVERSATION",
        recorded_at=t_snapshot + timedelta(days=15),
        funded_amount_gbp=0.0
    )
    o2 = CommercialFunnelOutcome(
        crn=crn,
        outcome_stage="APPLICATION_SUBMITTED",
        recorded_at=t_snapshot + timedelta(days=30),
        funded_amount_gbp=200000.0
    )
    o3 = CommercialFunnelOutcome(
        crn=crn,
        outcome_stage="FACILITY_FUNDED",
        recorded_at=t_snapshot + timedelta(days=45),
        funded_amount_gbp=200000.0
    )
    db.add_all([o1, o2, o3])
    db.commit()

    try:
        result = builder.build_dataset(
            crns=[crn],
            snapshot_dates={crn: [t_snapshot]},
            target=TARGET_BROKER_FUNDED,
            horizon_days=horizon_days,
            as_of_reference=now
        )

        # STRICT PROOF: Exactly one row for (crn, snapshot_date), not 3 rows
        assert result.sample_count == 1
        assert len(result.records) == 1
        assert result.records[0].crn == crn
        # Label is 1 because FACILITY_FUNDED occurred within horizon
        assert result.records[0].label == 1
        assert result.y[0] == 1

    finally:
        db.query(CommercialFunnelOutcome).delete()
        db.commit()
        db.close()


def test_proxy_label_new_charge_12m():
    """
    Verifies the proxy target 'new_charge_12m':
    When a new charge is registered within the horizon, label is 1; otherwise 0.
    """
    db = SessionLocal()
    builder = DatasetBuilder(db)
    crn = "08931201"
    now = datetime.now(timezone.utc)
    horizon_days = 180

    t_snapshot = now - timedelta(days=300)

    # Add a new charge registered inside the horizon (e.g. t_snapshot + 30 days)
    chg_sig = NormalizedSignal(
        crn=crn,
        signal_type="charge_creation",
        source_provider="companies_house",
        original_event_ref="proxy-test-new-chg-001",
        observation_timestamp=t_snapshot + timedelta(days=30),
        value_numeric=1.0,
        value_text="New fixed debenture charge registered",
        value_units="count",
        direction="neutral",
        confidence_score=0.90,
        verification_status="verified"
    )
    db.add(chg_sig)
    db.commit()

    try:
        result = builder.build_dataset(
            crns=[crn],
            snapshot_dates={crn: [t_snapshot]},
            target=TARGET_NEW_CHARGE_12M,
            horizon_days=horizon_days,
            as_of_reference=now
        )
        assert result.sample_count == 1
        assert result.y[0] == 1
        assert result.records[0].label == 1

    finally:
        db.delete(chg_sig)
        db.commit()
        db.close()
