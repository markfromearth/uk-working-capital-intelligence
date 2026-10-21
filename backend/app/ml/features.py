import math
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore
    NUMPY_AVAILABLE = False


@dataclass(frozen=True)
class FeatureDefinition:
    """
    Specification for an individual feature dimension.
    monotonic_direction:
      +1: strictly increasing relation (higher feature -> higher strain/demand)
      -1: strictly decreasing relation (higher feature -> lower strain/demand)
       0: unconstrained
    missing_policy:
      'indicator_and_nan': retains float('nan') and produces a '{name}_is_missing' binary feature
      'zero_if_none': counts/totals where absence represents 0 occurrences
    """
    name: str
    source_field: str
    description: str
    monotonic_direction: int
    missing_policy: str = "indicator_and_nan"
    default_if_missing: Optional[float] = None


# Canonical Feature Specification:
# Single source of truth across training dataset generation and real-time model inference.
# Avoids training/serving skew by enforcing identical extraction, ordering, and missing policies.
FEATURE_SPEC: List[FeatureDefinition] = [
    # 1. Financing & Charge Activity
    FeatureDefinition(
        name="charges_satisfied_90d",
        source_field="charges_satisfied_90d",
        description="Count of MR04/MR05 charge satisfactions in last 90 days (refinancing/discharge indicator)",
        monotonic_direction=1,
        missing_policy="zero_if_none",
        default_if_missing=0.0
    ),
    FeatureDefinition(
        name="charges_created_90d",
        source_field="charges_created_90d",
        description="Count of new charges registered in last 90 days (active lender security taking)",
        monotonic_direction=1,
        missing_policy="zero_if_none",
        default_if_missing=0.0
    ),
    FeatureDefinition(
        name="age_latest_charge_satisfaction_days",
        source_field="age_latest_charge_satisfaction_days",
        description="Days elapsed since most recent charge satisfaction (higher = older/less recent signal)",
        monotonic_direction=-1,
        missing_policy="indicator_and_nan"
    ),

    # 2. Commercial Credit & Payment Risk
    FeatureDefinition(
        name="dbt_current_days",
        source_field="dbt_current_days",
        description="Current Days Beyond Terms overdue (commercial payment delay)",
        monotonic_direction=1,
        missing_policy="indicator_and_nan"
    ),
    FeatureDefinition(
        name="dbt_trend_90d",
        source_field="dbt_trend_90d",
        description="90-day trajectory of Days Beyond Terms (positive = deteriorating trade credit)",
        monotonic_direction=1,
        missing_policy="indicator_and_nan"
    ),
    FeatureDefinition(
        name="adverse_insolvency_events_count",
        source_field="adverse_insolvency_events_count",
        description="Count of formal adverse credit or winding up petition records",
        monotonic_direction=1,
        missing_policy="zero_if_none",
        default_if_missing=0.0
    ),

    # 3. Workforce & Growth Dynamics
    FeatureDefinition(
        name="headcount_current",
        source_field="headcount_current",
        description="Current observed employee headcount",
        monotonic_direction=0,
        missing_policy="indicator_and_nan"
    ),
    FeatureDefinition(
        name="headcount_growth_90d_pct",
        source_field="headcount_growth_90d_pct",
        description="90-day percentage workforce expansion",
        monotonic_direction=0,
        missing_policy="indicator_and_nan"
    ),

    # 4. Corroboration & Observation Depth
    FeatureDefinition(
        name="corroboration_signal_count",
        source_field="corroboration_signal_count",
        description="Count of corroborating signals confirming the liquidity hypothesis",
        monotonic_direction=0,
        missing_policy="zero_if_none",
        default_if_missing=0.0
    ),
    FeatureDefinition(
        name="distinct_source_count",
        source_field="distinct_source_count",
        description="Number of independent provider sources corroborating the company",
        monotonic_direction=0,
        missing_policy="zero_if_none",
        default_if_missing=0.0
    ),
]


def _build_feature_names_and_constraints() -> Tuple[List[str], List[int]]:
    """Builds ordered lists of final feature names and their corresponding monotonic constraints."""
    names: List[str] = []
    constraints: List[int] = []

    # First pass: main features
    for f in FEATURE_SPEC:
        names.append(f.name)
        constraints.append(f.monotonic_direction)

    # Second pass: explicit missing indicators for features that use 'indicator_and_nan'
    for f in FEATURE_SPEC:
        if f.missing_policy == "indicator_and_nan":
            ind_name = f"{f.name}_is_missing"
            names.append(ind_name)
            constraints.append(0)  # Missingness indicator is unconstrained

    return names, constraints


FEATURE_NAMES, MONOTONIC_CONSTRAINTS = _build_feature_names_and_constraints()


def extract_features_dict(snapshot_or_dict: Any) -> Dict[str, float]:
    """
    Extracts canonical feature dictionary from a CompanyFeatureSnapshot OR equivalent dict/object.
    Enforces that missing values for continuous attributes are stored as NaN,
    paired with an explicit binary missing indicator feature (1.0 = missing, 0.0 = present).
    """
    result: Dict[str, float] = {}

    for f in FEATURE_SPEC:
        if isinstance(snapshot_or_dict, dict):
            raw_val = snapshot_or_dict.get(f.source_field)
        else:
            raw_val = getattr(snapshot_or_dict, f.source_field, None)

        if raw_val is None:
            if f.missing_policy == "zero_if_none":
                result[f.name] = float(f.default_if_missing if f.default_if_missing is not None else 0.0)
            else:
                result[f.name] = float("nan")
        else:
            try:
                result[f.name] = float(raw_val)
            except (ValueError, TypeError):
                result[f.name] = float("nan")

    # Add missing indicators
    for f in FEATURE_SPEC:
        if f.missing_policy == "indicator_and_nan":
            val = result.get(f.name)
            is_miss = 1.0 if (val is None or math.isnan(val)) else 0.0
            result[f"{f.name}_is_missing"] = is_miss

    return result


def extract_features_vector(snapshot_or_dict: Any) -> List[float]:
    """
    Extracts canonical feature vector as an ordered list of floats conforming strictly to FEATURE_NAMES.
    """
    fdict = extract_features_dict(snapshot_or_dict)
    return [fdict[name] for name in FEATURE_NAMES]


def get_feature_spec_hash() -> str:
    """
    Returns deterministic SHA256 digest of the canonical feature specification.
    Included in model metadata to detect training/serving feature spec mismatches.
    """
    canonical_summary = [
        {
            "name": f.name,
            "source_field": f.source_field,
            "direction": f.monotonic_direction,
            "missing_policy": f.missing_policy,
            "default": f.default_if_missing,
        }
        for f in FEATURE_SPEC
    ]
    serialized = json.dumps(canonical_summary, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
