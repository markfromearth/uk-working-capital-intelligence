from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set
from sqlalchemy.orm import Session

from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger
from backend.app.models.company import CompanyRegistry
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.models.signal import NormalizedSignal
from backend.app.services.feature_store import FeatureStoreService
from backend.app.ml.features import (
    FEATURE_NAMES,
    extract_features_dict,
    extract_features_vector,
)

TARGET_BROKER_FUNDED = "broker_funded"
TARGET_NEW_CHARGE_12M = "new_charge_12m"
VALID_TARGETS = (TARGET_BROKER_FUNDED, TARGET_NEW_CHARGE_12M)


@dataclass
class DatasetRecord:
    """One observation row in a time-aware tabular dataset."""
    crn: str
    snapshot_date: datetime
    features: List[float]
    feature_dict: Dict[str, float]
    label: int
    target: str
    is_censored: bool = False


@dataclass
class DatasetBuildResult:
    """Outcome of dataset construction with rigorous censoring statistics."""
    records: List[DatasetRecord]
    X: List[List[float]]
    y: List[int]
    crns: List[str]
    snapshot_dates: List[datetime]
    feature_names: List[str]
    target: str
    horizon_days: int
    total_snapshots_examined: int
    censored_dropped_count: int
    positive_count: int
    negative_count: int

    @property
    def sample_count(self) -> int:
        return len(self.records)

    @property
    def distinct_company_count(self) -> int:
        return len(set(self.crns))

    @property
    def positive_rate(self) -> float:
        return (self.positive_count / self.sample_count) if self.sample_count > 0 else 0.0


class DatasetBuilder:
    """
    Constructs leak-free, time-aware machine learning datasets.
    
    Guarantees:
    1. Strictly one row per (crn, snapshot_date).
    2. Point-in-time features computed as-of snapshot_date with zero future signal leakage.
    3. Horizon-based outcome labelling: event occurs strictly within (snapshot_date, snapshot_date + horizon].
    4. Right-censoring: Snapshots whose horizon has not fully elapsed relative to as_of_reference are dropped.
    5. Funnel stages collapse: Multiple stage records for a company collapse into one labelled event per snapshot.
    """

    def __init__(self, db: Session):
        self.db = db
        self.feature_store = FeatureStoreService(db)

    def build_dataset(
        self,
        crns: Optional[List[str]] = None,
        snapshot_dates: Optional[Dict[str, List[datetime]]] = None,
        target: str = TARGET_BROKER_FUNDED,
        horizon_days: int = 180,
        as_of_reference: Optional[datetime] = None,
        default_snapshots_per_company: int = 3,
        step_days: int = 90
    ) -> DatasetBuildResult:
        """
        Builds dataset for training or evaluation.
        
        Args:
            crns: Optional list of company CRNs. If omitted, queries all registered companies.
            snapshot_dates: Optional mapping of CRN -> list of snapshot datetimes.
                            If omitted, generates historical quarterly snapshot points.
            target: "broker_funded" (conversion outcome) or "new_charge_12m" (demand proxy).
            horizon_days: Observation horizon in days following snapshot_date.
            as_of_reference: Reference 'today' timestamp. Defaults to UTC now.
            default_snapshots_per_company: Number of historical snapshot points per company if auto-generating.
            step_days: Spacing in days between auto-generated snapshot points.
        """
        if target not in VALID_TARGETS:
            raise ValueError(f"Unknown target '{target}'. Allowed: {VALID_TARGETS}")

        ref_time = as_of_reference or datetime.now(timezone.utc)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)

        # Resolve CRNs
        if crns is None:
            db_companies = self.db.query(CompanyRegistry.crn).all()
            target_crns = [c[0] for c in db_companies]
        else:
            target_crns = []
            for raw in crns:
                norm, valid, _ = normalize_uk_crn(raw)
                if valid and norm:
                    target_crns.append(norm)

        records: List[DatasetRecord] = []
        total_examined = 0
        censored_dropped = 0
        positives = 0
        negatives = 0

        for crn in target_crns:
            # Determine candidate snapshot dates for this company
            if snapshot_dates and crn in snapshot_dates:
                candidate_dates = snapshot_dates[crn]
            else:
                candidate_dates = self._auto_generate_candidate_snapshots(
                    crn=crn,
                    ref_time=ref_time,
                    horizon_days=horizon_days,
                    num_snapshots=default_snapshots_per_company,
                    step_days=step_days
                )

            # Ensure chronological order and deduplication
            seen_dates: Set[str] = set()
            for s_date in candidate_dates:
                if s_date.tzinfo is None:
                    s_date = s_date.replace(tzinfo=timezone.utc)

                d_key = s_date.strftime("%Y-%m-%d")
                if d_key in seen_dates:
                    continue
                seen_dates.add(d_key)

                total_examined += 1

                # Right-censoring evaluation:
                # The horizon must have fully elapsed prior to ref_time
                horizon_end = s_date + timedelta(days=horizon_days)
                if horizon_end > ref_time:
                    # Snapshot cannot be honestly evaluated because the full horizon has not elapsed
                    censored_dropped += 1
                    continue

                # Point-in-time feature extraction as of snapshot_date
                snapshot = self.feature_store.compute_feature_snapshot(crn, as_of_timestamp=s_date)
                f_dict = extract_features_dict(snapshot)
                f_vec = [f_dict[name] for name in FEATURE_NAMES]

                # Evaluate label according to selected target definition
                label = self._evaluate_label(
                    crn=crn,
                    snapshot_date=s_date,
                    horizon_end=horizon_end,
                    target=target
                )

                if label == 1:
                    positives += 1
                else:
                    negatives += 1

                records.append(DatasetRecord(
                    crn=crn,
                    snapshot_date=s_date,
                    features=f_vec,
                    feature_dict=f_dict,
                    label=label,
                    target=target,
                    is_censored=False
                ))

        X = [r.features for r in records]
        y = [r.label for r in records]
        out_crns = [r.crn for r in records]
        out_dates = [r.snapshot_date for r in records]

        logger.info(
            f"Built dataset ({target}): {len(records)} rows from {len(set(out_crns))} CRNs "
            f"({positives} pos, {negatives} neg). Censored dropped: {censored_dropped}/{total_examined}."
        )

        return DatasetBuildResult(
            records=records,
            X=X,
            y=y,
            crns=out_crns,
            snapshot_dates=out_dates,
            feature_names=FEATURE_NAMES,
            target=target,
            horizon_days=horizon_days,
            total_snapshots_examined=total_examined,
            censored_dropped_count=censored_dropped,
            positive_count=positives,
            negative_count=negatives
        )

    def _auto_generate_candidate_snapshots(
        self,
        crn: str,
        ref_time: datetime,
        horizon_days: int,
        num_snapshots: int,
        step_days: int
    ) -> List[datetime]:
        """
        Generates candidate snapshot points stepping backwards from (ref_time - horizon_days).
        Ensures candidates have potential to survive censoring unless deliberately testing boundary cases.
        """
        dates: List[datetime] = []
        base = ref_time - timedelta(days=horizon_days)
        for i in range(num_snapshots):
            dt = base - timedelta(days=i * step_days)
            dates.append(dt)
        return dates

    def _evaluate_label(
        self,
        crn: str,
        snapshot_date: datetime,
        horizon_end: datetime,
        target: str
    ) -> int:
        """
        Evaluates binary target label strictly within the post-snapshot window (snapshot_date, horizon_end].
        Collapses any multiple stage records into a single 1 if any target event occurred.
        """
        if target == TARGET_BROKER_FUNDED:
            # Target = 1 if company reached FACILITY_OFFERED or FACILITY_FUNDED within the horizon
            qualifying_outcome = self.db.query(CommercialFunnelOutcome).filter(
                CommercialFunnelOutcome.crn == crn,
                CommercialFunnelOutcome.recorded_at > snapshot_date,
                CommercialFunnelOutcome.recorded_at <= horizon_end,
                CommercialFunnelOutcome.outcome_stage.in_(("FACILITY_OFFERED", "FACILITY_FUNDED"))
            ).first()
            return 1 if qualifying_outcome is not None else 0

        elif target == TARGET_NEW_CHARGE_12M:
            # Target = 1 if a new charge (MR01 / charge registration) occurred within the horizon
            # Explicit proxy: measures financing DEMAND / new borrowing facility, not broker conversion
            new_charge_sig = self.db.query(NormalizedSignal).filter(
                NormalizedSignal.crn == crn,
                NormalizedSignal.observation_timestamp > snapshot_date,
                NormalizedSignal.observation_timestamp <= horizon_end,
                NormalizedSignal.signal_type.in_(("charge_creation", "charge_registered")),
                NormalizedSignal.verification_status != "disputed",
                NormalizedSignal.verification_status != "superseded"
            ).first()
            return 1 if new_charge_sig is not None else 0

        return 0
