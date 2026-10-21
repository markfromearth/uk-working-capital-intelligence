"""
Machine Learning Subsystem for UK Working-Capital Intelligence & Prospect Scoring Platform.
Provides canonical feature extraction, time-aware dataset construction, honest walk-forward evaluation,
and shadow-mode model logging and promotion gates.
"""

from backend.app.ml.features import (
    FEATURE_SPEC,
    FEATURE_NAMES,
    MONOTONIC_CONSTRAINTS,
    extract_features_dict,
    extract_features_vector,
    get_feature_spec_hash,
)

__all__ = [
    "FEATURE_SPEC",
    "FEATURE_NAMES",
    "MONOTONIC_CONSTRAINTS",
    "extract_features_dict",
    "extract_features_vector",
    "get_feature_spec_hash",
]
