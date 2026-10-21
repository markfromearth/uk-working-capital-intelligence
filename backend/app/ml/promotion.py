import os
import json
import random
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.ml.train import compute_top_k_metrics

DEFAULT_MODEL_DIR = "ml_models"


def bootstrap_cluster_ci(
    y_true: List[int],
    cand_scores: List[float],
    rules_scores: List[float],
    crns: List[str],
    num_bootstraps: int = 500,
    k: int = 10,
    seed: int = 42
) -> Dict[str, Any]:
    """
    Computes clustered bootstrap confidence intervals by company (CRN).
    Resamples distinct companies with replacement, preserving within-company correlation.
    Evaluates whether the 95% CI of (candidate - rules) improvement strictly excludes zero.
    """
    rng = random.Random(seed)
    n = len(y_true)
    if n == 0 or len(crns) != n:
        return {"bootstrap_ci_valid": False, "reason": "Insufficient or mismatched data"}

    # Group record indices by CRN
    company_to_indices: Dict[str, List[int]] = {}
    for idx, c in enumerate(crns):
        company_to_indices.setdefault(c, []).append(idx)

    unique_companies = list(company_to_indices.keys())
    num_unique = len(unique_companies)

    if num_unique < 5:
        return {
            "bootstrap_ci_valid": False,
            "reason": f"Need at least 5 distinct companies for cluster bootstrap; found {num_unique}."
        }

    prec_diffs: List[float] = []
    brier_diffs: List[float] = []  # rules_brier - cand_brier (positive = candidate is better)

    for _ in range(num_bootstraps):
        sampled_companies = [rng.choice(unique_companies) for _ in range(num_unique)]
        sample_indices: List[int] = []
        for c in sampled_companies:
            sample_indices.extend(company_to_indices[c])

        b_y = [y_true[i] for i in sample_indices]
        b_cand = [cand_scores[i] for i in sample_indices]
        b_rules = [rules_scores[i] for i in sample_indices]

        # Top-k precision
        cand_top = compute_top_k_metrics(b_y, b_cand, k_values=[k])
        rules_top = compute_top_k_metrics(b_y, b_rules, k_values=[k])

        p_cand = cand_top.get(f"top_{k}", {}).get("precision", 0.0)
        p_rules = rules_top.get(f"top_{k}", {}).get("precision", 0.0)
        prec_diffs.append(p_cand - p_rules)

        # Brier score (lower is better, so diff = rules - cand > 0 means improvement)
        brier_cand = sum((b_cand[i] - b_y[i]) ** 2 for i in range(len(b_y))) / float(len(b_y))
        brier_rules = sum((b_rules[i] - b_y[i]) ** 2 for i in range(len(b_y))) / float(len(b_y))
        brier_diffs.append(brier_rules - brier_cand)

    prec_diffs.sort()
    brier_diffs.sort()

    # 95% Confidence Interval (2.5th and 97.5th percentiles)
    lo_idx = int(num_bootstraps * 0.025)
    hi_idx = int(num_bootstraps * 0.975)

    prec_ci = (round(prec_diffs[lo_idx], 4), round(prec_diffs[hi_idx], 4))
    brier_ci = (round(brier_diffs[lo_idx], 4), round(brier_diffs[hi_idx], 4))

    # Improvement is statistically reliable if the lower bound of the CI > 0
    prec_excludes_zero = prec_ci[0] > 0.0
    brier_excludes_zero = brier_ci[0] > 0.0

    return {
        "bootstrap_ci_valid": True,
        "num_bootstraps": num_bootstraps,
        "cluster_companies_count": num_unique,
        "precision_at_top_k_delta_ci_95": {"lower": prec_ci[0], "upper": prec_ci[1]},
        "brier_delta_ci_95": {"lower": brier_ci[0], "upper": brier_ci[1]},
        "precision_excludes_zero": prec_excludes_zero,
        "brier_excludes_zero": brier_excludes_zero,
        "ci_statistically_significant": bool(prec_excludes_zero and brier_excludes_zero)
    }


def get_latest_model_card(model_dir: str = DEFAULT_MODEL_DIR) -> Optional[Dict[str, Any]]:
    """Finds and loads the latest metrics.json model card from disk."""
    if not os.path.exists(model_dir):
        return None

    # Search for version subdirectories containing metrics.json
    candidates = []
    for item in os.listdir(model_dir):
        item_path = os.path.join(model_dir, item)
        if os.path.isdir(item_path):
            card_path = os.path.join(item_path, "metrics.json")
            if os.path.exists(card_path):
                try:
                    mtime = os.path.getmtime(card_path)
                    candidates.append((mtime, card_path))
                except OSError:
                    pass

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_card_path = candidates[0][1]

    try:
        with open(latest_card_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not read model card from {latest_card_path}: {e}")
        return None
