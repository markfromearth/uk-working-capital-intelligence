import math
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

from backend.app.ml.features import FEATURE_NAMES, extract_features_vector
from backend.app.ml.dataset import DatasetBuildResult, DatasetRecord, TARGET_NEW_CHARGE_12M


def generate_synthetic_ml_dataset(
    num_companies: int = 60,
    snapshots_per_company: int = 4,
    positive_rate_target: float = 0.25,
    seed: int = 42
) -> DatasetBuildResult:
    """
    Generates a deterministic synthetic evaluation dataset IN-MEMORY with a fixed seed.
    NEVER writes to the persistent application database.
    Used exclusively for testing training pipelines, metrics calculation, and promotion gates.
    """
    rng = random.Random(seed)
    records: List[DatasetRecord] = []
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    crns = [f"{10000000 + i}" for i in range(num_companies)]

    for crn in crns:
        # Base company risk tier (fixed per company)
        company_latent_strain = rng.uniform(0.0, 1.0)

        for s_idx in range(snapshots_per_company):
            # Quarterly snapshots stepping forward
            s_date = base_time + timedelta(days=s_idx * 90)

            # Features correlate with latent strain
            dbt_missing = rng.random() < 0.15
            dbt_val = (company_latent_strain * 45.0 + rng.gauss(0, 5)) if not dbt_missing else None
            if dbt_val is not None:
                dbt_val = max(0.0, dbt_val)

            chg_sat = 1 if (company_latent_strain > 0.6 and rng.random() < 0.7) else 0
            chg_created = 1 if (company_latent_strain > 0.5 and rng.random() < 0.5) else 0
            age_chg = rng.uniform(10.0, 120.0) if chg_sat > 0 else None

            adverse = 1 if (company_latent_strain > 0.8 and rng.random() < 0.4) else 0
            hc_val = int(rng.uniform(10, 200)) if rng.random() > 0.2 else None
            hc_growth = rng.uniform(-10.0, 30.0) if hc_val is not None else None

            dummy_snapshot = {
                "charges_satisfied_90d": chg_sat,
                "charges_created_90d": chg_created,
                "age_latest_charge_satisfaction_days": age_chg,
                "dbt_current_days": dbt_val,
                "dbt_trend_90d": (rng.gauss(2.0, 3.0) if dbt_val is not None else None),
                "adverse_insolvency_events_count": adverse,
                "headcount_current": hc_val,
                "headcount_growth_90d_pct": hc_growth,
                "corroboration_signal_count": 2 if company_latent_strain > 0.5 else 0,
                "distinct_source_count": 2 if company_latent_strain > 0.5 else 1,
            }

            from backend.app.ml.features import extract_features_dict
            f_dict = extract_features_dict(dummy_snapshot)
            f_vec = [f_dict[name] for name in FEATURE_NAMES]

            # Label generation: higher strain = higher probability of financing event within 180 days
            # Latent log-odds
            z = -2.0 + (company_latent_strain * 3.5) + (chg_sat * 0.8) + ((dbt_val or 0.0) * 0.04)
            prob = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, z))))
            label = 1 if rng.random() < prob else 0

            records.append(DatasetRecord(
                crn=crn,
                snapshot_date=s_date,
                features=f_vec,
                feature_dict=f_dict,
                label=label,
                target=TARGET_NEW_CHARGE_12M,
                is_censored=False
            ))

    pos_count = sum(r.label for r in records)
    neg_count = len(records) - pos_count

    return DatasetBuildResult(
        records=records,
        X=[r.features for r in records],
        y=[r.label for r in records],
        crns=[r.crn for r in records],
        snapshot_dates=[r.snapshot_date for r in records],
        feature_names=FEATURE_NAMES,
        target=TARGET_NEW_CHARGE_12M,
        horizon_days=180,
        total_snapshots_examined=len(records),
        censored_dropped_count=0,
        positive_count=pos_count,
        negative_count=neg_count
    )
