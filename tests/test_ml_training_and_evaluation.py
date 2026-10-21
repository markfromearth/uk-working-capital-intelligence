import os
import json
import pytest
from datetime import datetime, timezone

from backend.app.ml.train import (
    HonestModelEvaluator,
    compute_top_k_metrics,
    compute_calibration_table,
    get_git_sha
)
from tests.synthetic_ml_fixtures import generate_synthetic_ml_dataset


def test_training_refusal_guard():
    """
    Verifies that the trainer strictly refuses to fit a toy or overfitted model
    if there are fewer than N distinct companies or fewer than min_positives.
    """
    evaluator = HonestModelEvaluator(min_distinct_companies=200, min_positives=30)

    # Dataset with only 15 companies
    toy_dataset = generate_synthetic_ml_dataset(num_companies=15, snapshots_per_company=2, seed=99)
    res = evaluator.train_and_evaluate(toy_dataset)

    assert res["trained"] is False
    assert "Insufficient data volume" in res["refusal_reason"]
    assert res["distinct_companies"] == 15
    assert res["min_companies_threshold"] == 200


def test_top_k_metrics_and_calibration_table():
    """
    Verifies calculation of precision@k, lift@k, and calibration table.
    """
    y_true = [1, 1, 0, 1, 0, 0, 0, 0, 1, 0] # 4 positives out of 10 (base rate = 0.40)
    y_scores = [0.95, 0.90, 0.85, 0.80, 0.70, 0.50, 0.40, 0.30, 0.20, 0.10]

    top_k = compute_top_k_metrics(y_true, y_scores, k_values=[2, 4])
    # Top 2: indices 0 and 1 (both are 1) -> precision = 1.0, lift = 1.0 / 0.4 = 2.5
    assert top_k["top_2"]["precision"] == 1.0
    assert top_k["top_2"]["lift"] == 2.5

    # Top 4: indices 0, 1, 2, 3 -> three 1s, one 0 -> precision = 0.75, lift = 0.75 / 0.4 = 1.88
    assert top_k["top_4"]["precision"] == 0.75

    # Calibration table
    calib = compute_calibration_table(y_true, y_scores, num_bins=2)
    assert len(calib) == 2
    assert calib[0]["count"] > 0
    assert calib[1]["count"] > 0


def test_honest_model_evaluation_and_model_card_generation(tmp_path):
    """
    Verifies end-to-end model evaluation, rules baseline comparison on the same held-out rows,
    and model card generation with all mandatory provenance fields.
    """
    # Use synthetic fixture with configurable threshold
    dataset = generate_synthetic_ml_dataset(num_companies=60, snapshots_per_company=4, seed=42)

    evaluator = HonestModelEvaluator(
        min_distinct_companies=50, # Set threshold lower for test
        min_positives=15,
        model_dir=str(tmp_path)
    )

    test_version = "v2.0.0-test-eval"
    res = evaluator.train_and_evaluate(dataset, model_version=test_version)

    assert res["trained"] is True
    assert res["model_version"] == test_version

    card = res["model_card"]
    assert "git_sha" in card
    assert "feature_spec_hash" in card
    assert "data_summary" in card
    assert "candidate_evaluations" in card
    assert "promotion_gate" in card
    assert "HONEST DATA NOTICE" in card["data_caveat"]

    # Verify head-to-head comparison exists for rules baseline vs candidate ML models
    candidates = card["candidate_evaluations"]
    assert "rules_engine_baseline" in candidates
    assert "regularized_logistic_regression" in candidates
    assert "hist_gradient_boosting" in candidates
    assert "calibrated_hist_gradient_boosting" in candidates

    # Verify metrics exist for rules baseline
    rules_res = candidates["rules_engine_baseline"]
    assert "roc_auc" in rules_res
    assert "brier_score" in rules_res
    assert "top_k_metrics" in rules_res
    assert "calibration_table" in rules_res

    # Verify metrics file was written to disk
    metrics_path = os.path.join(str(tmp_path), test_version, "metrics.json")
    assert os.path.exists(metrics_path)

    with open(metrics_path, "r") as f:
        loaded_card = json.load(f)
    assert loaded_card["model_version"] == test_version
    assert loaded_card["data_summary"]["distinct_companies"] == dataset.distinct_company_count
