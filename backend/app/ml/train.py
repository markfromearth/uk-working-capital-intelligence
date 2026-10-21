import os
import json
import math
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        roc_auc_score,
        average_precision_score,
        brier_score_loss,
    )
    SKLEARN_AVAILABLE = True
except ImportError:
    np = None  # type: ignore
    joblib = None  # type: ignore
    SKLEARN_AVAILABLE = False

from backend.app.core.logging import logger
from backend.app.ml.features import (
    FEATURE_NAMES,
    MONOTONIC_CONSTRAINTS,
    get_feature_spec_hash,
)
from backend.app.ml.dataset import DatasetBuildResult, TARGET_NEW_CHARGE_12M


DEFAULT_MIN_COMPANIES = 200
DEFAULT_MIN_POSITIVES = 30
MODEL_VERSION_TAG = "v2.0.0-production-candidate"


def get_git_sha() -> str:
    """Retrieves current Git commit SHA for model reproducibility audit."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown_git_sha"


def compute_top_k_metrics(
    y_true: List[int],
    y_scores: List[float],
    k_values: List[int] = [10, 25, 50]
) -> Dict[str, Dict[str, float]]:
    """
    Computes precision@k and lift@k because commercial brokers act on ranked shortlists.
    """
    n = len(y_true)
    if n == 0:
        return {}

    overall_pos_rate = sum(y_true) / float(n)
    # Sort indices by score descending
    sorted_indices = sorted(range(n), key=lambda i: y_scores[i], reverse=True)

    results: Dict[str, Dict[str, float]] = {}
    for k in k_values:
        actual_k = min(k, n)
        if actual_k == 0:
            continue
        top_k_indices = sorted_indices[:actual_k]
        top_k_positives = sum(y_true[i] for i in top_k_indices)
        precision = top_k_positives / float(actual_k)
        lift = (precision / overall_pos_rate) if overall_pos_rate > 0 else 1.0

        results[f"top_{k}"] = {
            "k": actual_k,
            "precision": round(precision, 4),
            "lift": round(lift, 2),
            "hits": top_k_positives
        }

    return results


def compute_calibration_table(
    y_true: List[int],
    y_probs: List[float],
    num_bins: int = 5
) -> List[Dict[str, Any]]:
    """
    Computes empirical calibration bins: mean predicted probability vs observed fraction.
    """
    n = len(y_true)
    if n == 0:
        return []

    # Sort pairs by predicted probability
    pairs = sorted(zip(y_probs, y_true), key=lambda p: p[0])
    bin_size = max(1, n // num_bins)
    table: List[Dict[str, Any]] = []

    for b in range(num_bins):
        start = b * bin_size
        end = (b + 1) * bin_size if b < num_bins - 1 else n
        slice_pairs = pairs[start:end]
        if not slice_pairs:
            continue

        bin_probs = [p[0] for p in slice_pairs]
        bin_trues = [p[1] for p in slice_pairs]

        table.append({
            "bin": b + 1,
            "count": len(slice_pairs),
            "prob_min": round(float(min(bin_probs)), 4),
            "prob_max": round(float(max(bin_probs)), 4),
            "prob_mean": round(float(sum(bin_probs) / len(bin_probs)), 4),
            "observed_fraction": round(float(sum(bin_trues) / len(bin_trues)), 4),
        })

    return table


class HonestModelEvaluator:
    """
    Trains candidate ML models and evaluates them against the explainable rules engine
    using time-ordered splits and disjoint company clusters to ensure zero data leakage.
    """

    def __init__(
        self,
        min_distinct_companies: int = DEFAULT_MIN_COMPANIES,
        min_positives: int = DEFAULT_MIN_POSITIVES,
        model_dir: str = "ml_models"
    ):
        self.min_companies = min_distinct_companies
        self.min_positives = min_positives
        self.model_dir = model_dir

    def train_and_evaluate(
        self,
        dataset: DatasetBuildResult,
        rules_scorer_fn: Optional[Any] = None,
        test_fraction: float = 0.25,
        val_calibration_fraction: float = 0.20,
        model_version: str = MODEL_VERSION_TAG
    ) -> Dict[str, Any]:
        """
        Executes honest walk-forward evaluation across candidate models and rules baseline.
        
        Refusal guard: Refuses to train if distinct companies or positives fall below thresholds.
        """
        if not SKLEARN_AVAILABLE:
            return {
                "trained": False,
                "refusal_reason": "scikit-learn is not installed in the environment."
            }

        # 1. Refusal Guard
        distinct_companies = dataset.distinct_company_count
        pos_count = dataset.positive_count

        if distinct_companies < self.min_companies or pos_count < self.min_positives:
            refusal_msg = (
                f"Insufficient data volume for honest ML evaluation: "
                f"found {distinct_companies} companies (minimum required: {self.min_companies}), "
                f"and {pos_count} positive events (minimum required: {self.min_positives}). "
                f"Refusing to fit toy or overfitted model."
            )
            logger.warning(refusal_msg)
            return {
                "trained": False,
                "refusal_reason": refusal_msg,
                "distinct_companies": distinct_companies,
                "min_companies_threshold": self.min_companies,
                "positive_count": pos_count,
                "min_positives_threshold": self.min_positives,
                "sample_count": dataset.sample_count
            }

        # 2. Split Data: Time-ordered and Grouped by Company (Expanding Window + Group Disjoint)
        sorted_records = sorted(dataset.records, key=lambda r: r.snapshot_date)
        all_crns = sorted(list(set(r.crn for r in sorted_records)))

        # Partition distinct CRNs into train companies and test companies
        num_test_companies = max(1, int(len(all_crns) * test_fraction))
        test_crns_set = set(all_crns[:num_test_companies])
        train_crns_set = set(all_crns[num_test_companies:])

        # Time cutoff for expanding window: evaluate test set on future observation points
        split_idx = int(len(sorted_records) * (1.0 - test_fraction))
        split_cutoff_date = sorted_records[split_idx].snapshot_date

        # Test records: disjoint companies, evaluated on the out-of-sample forward time period
        test_records = [r for r in sorted_records if r.crn in test_crns_set and r.snapshot_date >= split_cutoff_date]
        if not test_records:
            # Fallback to all records of the test companies if dates are clustered
            test_records = [r for r in sorted_records if r.crn in test_crns_set]

        # Train records: strictly from the training companies prior to/at the split cutoff
        train_val_records = [r for r in sorted_records if r.crn in train_crns_set and r.snapshot_date <= split_cutoff_date]
        if not train_val_records:
            train_val_records = [r for r in sorted_records if r.crn in train_crns_set]

        # Further split train_val into train_fit and train_calib (held-out calibration slice)
        num_calib = max(1, int(len(train_val_records) * val_calibration_fraction))
        fit_records = train_val_records[:-num_calib] if len(train_val_records) > num_calib else train_val_records
        calib_records = train_val_records[-num_calib:] if len(train_val_records) > num_calib else []

        X_fit = np.array([r.features for r in fit_records])
        y_fit = np.array([r.label for r in fit_records])

        X_calib = np.array([r.features for r in calib_records]) if calib_records else None
        y_calib = np.array([r.label for r in calib_records]) if calib_records else None

        X_test = np.array([r.features for r in test_records])
        y_test = [r.label for r in test_records]
        y_test_arr = np.array(y_test)

        # 3. Fit Candidate 1: Regularized Logistic Regression (Baseline)
        lr_pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(C=0.1, max_iter=500, random_state=42))
        ])
        lr_pipeline.fit(X_fit, y_fit)
        preds_lr = lr_pipeline.predict_proba(X_test)[:, 1].tolist()

        # 4. Fit Candidate 2: HistGradientBoosting with Monotonic Constraints
        hgb = HistGradientBoostingClassifier(
            monotonic_cst=MONOTONIC_CONSTRAINTS,
            max_iter=100,
            learning_rate=0.05,
            min_samples_leaf=max(5, len(X_fit) // 20),
            random_state=42
        )
        hgb.fit(X_fit, y_fit)
        preds_hgb = hgb.predict_proba(X_test)[:, 1].tolist()

        # 5. Fit Candidate 3: Calibrated HistGradientBoosting
        # Calibrate on held-out calibration slice, NOT training data
        if calib_records and len(np.unique(y_calib)) >= 2:
            try:
                from sklearn.frozen import FrozenEstimator
                frozen_hgb = FrozenEstimator(hgb)
                calibrated_hgb = CalibratedClassifierCV(
                    estimator=frozen_hgb,
                    method="isotonic" if len(y_calib) >= 50 else "sigmoid"
                )
            except ImportError:
                calibrated_hgb = CalibratedClassifierCV(
                    estimator=hgb,
                    method="isotonic" if len(y_calib) >= 50 else "sigmoid",
                    cv="prefit"
                )
            calibrated_hgb.fit(X_calib, y_calib)
            preds_calib_hgb = calibrated_hgb.predict_proba(X_test)[:, 1].tolist()
            best_model = calibrated_hgb
        else:
            calibrated_hgb = None
            preds_calib_hgb = preds_hgb
            best_model = hgb

        # 6. Evaluate Rules Engine Baseline on the exact same held-out rows
        if rules_scorer_fn:
            rules_scores = [float(rules_scorer_fn(r.crn, r.snapshot_date)) for r in test_records]
        else:
            # Fallback rules heuristic: normalised to [0, 1]
            rules_scores = []
            for r in test_records:
                dbt = r.feature_dict.get("dbt_current_days") or 0.0
                chg = r.feature_dict.get("charges_satisfied_90d") or 0.0
                heur = min(1.0, 0.15 + (chg * 0.25) + (dbt * 0.012))
                rules_scores.append(round(heur, 4))

        # 7. Compute Metrics for all models on out-of-sample test rows
        def _score_model_predictions(scores: List[float], name: str) -> Dict[str, Any]:
            scores_arr = np.array(scores)
            try:
                auc = round(float(roc_auc_score(y_test_arr, scores_arr)), 4)
            except Exception:
                auc = 0.5
            try:
                pr_auc = round(float(average_precision_score(y_test_arr, scores_arr)), 4)
            except Exception:
                pr_auc = round(float(sum(y_test) / len(y_test)), 4)
            brier = round(float(brier_score_loss(y_test_arr, scores_arr)), 4)

            top_k = compute_top_k_metrics(y_test, scores)
            calib_table = compute_calibration_table(y_test, scores)

            return {
                "model_name": name,
                "roc_auc": auc,
                "pr_auc": pr_auc,
                "brier_score": brier,
                "top_k_metrics": top_k,
                "calibration_table": calib_table
            }

        candidate_results = {
            "rules_engine_baseline": _score_model_predictions(rules_scores, "Rules Engine (Explainable Baseline)"),
            "regularized_logistic_regression": _score_model_predictions(preds_lr, "Regularized Logistic Regression"),
            "hist_gradient_boosting": _score_model_predictions(preds_hgb, "HistGradientBoosting (Monotonic Constrained)"),
            "calibrated_hist_gradient_boosting": _score_model_predictions(preds_calib_hgb, "Calibrated HistGradientBoosting (Held-out Calibrated)")
        }

        # 8. Check Promotion Gate Criteria
        # Must beat rules baseline on top_k precision AND Brier score
        rules_top10_prec = candidate_results["rules_engine_baseline"]["top_k_metrics"].get("top_10", {}).get("precision", 0.0)
        rules_brier = candidate_results["rules_engine_baseline"]["brier_score"]

        best_candidate = candidate_results["calibrated_hist_gradient_boosting"]
        cand_top10_prec = best_candidate["top_k_metrics"].get("top_10", {}).get("precision", 0.0)
        cand_brier = best_candidate["brier_score"]

        beats_top_k = cand_top10_prec > rules_top10_prec
        beats_brier = cand_brier < rules_brier
        promotion_gate_passed = bool(beats_top_k and beats_brier)

        # 9. Save Best Model and Model Card / Metrics Card
        model_version_dir = os.path.join(self.model_dir, model_version)
        os.makedirs(model_version_dir, exist_ok=True)

        model_card = {
            "model_version": model_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": get_git_sha(),
            "feature_spec_hash": get_feature_spec_hash(),
            "target": dataset.target,
            "horizon_days": dataset.horizon_days,
            "label_definition": (
                "New charge registration (MR01) within horizon (Financing Demand Proxy)"
                if dataset.target == TARGET_NEW_CHARGE_12M
                else "Commercial facility offered or funded by broker"
            ),
            "data_summary": {
                "total_rows": dataset.sample_count,
                "distinct_companies": distinct_companies,
                "positive_count": pos_count,
                "positive_rate": round(dataset.positive_rate, 4),
                "censored_dropped_count": dataset.censored_dropped_count,
                "train_rows": len(fit_records),
                "calibration_rows": len(calib_records) if calib_records else 0,
                "test_out_of_sample_rows": len(test_records),
                "test_distinct_companies": len(test_crns_set),
            },
            "candidate_evaluations": candidate_results,
            "promotion_gate": {
                "passed": promotion_gate_passed,
                "criteria": "Must beat rules baseline on out-of-sample top-10 precision AND Brier score",
                "rules_baseline": {
                    "top_10_precision": rules_top10_prec,
                    "brier_score": rules_brier
                },
                "best_candidate": {
                    "top_10_precision": cand_top10_prec,
                    "brier_score": cand_brier
                },
                "precision_improvement": round(cand_top10_prec - rules_top10_prec, 4),
                "brier_improvement": round(rules_brier - cand_brier, 4)
            },
            "data_caveat": (
                "HONEST DATA NOTICE: Metrics reflect evaluated data volume. "
                "Models trained on synthetic test fixtures or low company counts "
                "must remain strictly in shadow mode until real multi-year commercial conversion data is accumulated."
            )
        }

        metrics_file = os.path.join(model_version_dir, "metrics.json")
        with open(metrics_file, "w") as f:
            json.dump(model_card, f, indent=2)

        # Save model weights to version directory
        model_weights_path = os.path.join(model_version_dir, "model.pkl")
        joblib.dump(best_model, model_weights_path)

        logger.info(
            f"Honest evaluation complete. Metrics written to {metrics_file}. "
            f"Promotion gate passed: {promotion_gate_passed}."
        )

        return {
            "trained": True,
            "model_version": model_version,
            "model_card": model_card,
            "promotion_gate_passed": promotion_gate_passed,
            "metrics_path": metrics_file
        }
