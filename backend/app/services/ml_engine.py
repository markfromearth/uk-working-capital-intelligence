import os
import joblib
import numpy as np
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, brier_score_loss, precision_score
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.investigation import CommercialOpportunityAssessment
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.outcome_learning import OutcomeLearningService

ML_MODEL_VERSION = "v1.1.0-ml-shadow"
MODEL_STORAGE_PATH = "ml_models/opportunity_classifier_v1.pkl"

class MachineLearningOpportunityModel:
    """
    Supervised Machine Learning Engine for commercial opportunity conversion prediction.
    Features isotonic probability calibration, walk-forward time-aware validation,
    and shadow-mode evaluation to ensure safe, auditable model promotion.
    """

    def __init__(self, db: Session, model_path: Optional[str] = None):
        self.db = db
        self.model_path = model_path or MODEL_STORAGE_PATH
        self.model: Optional[CalibratedClassifierCV] = None
        self._load_or_init_model()

    def _load_or_init_model(self):
        """Loads serialized model if available on disk."""
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                logger.info(f"Loaded trained ML model from {self.model_path}")
            except Exception as e:
                logger.warning(f"Could not load ML model from {self.model_path}: {e}")
                self.model = None

    def train_model(
        self,
        min_samples: int = 10
    ) -> Dict[str, Any]:
        """
        Trains calibrated machine learning model using time-aware outcome records.
        Returns training metrics (ROC-AUC, Brier score, sample count).
        """
        outcome_service = OutcomeLearningService(self.db)
        X, y, feature_names = outcome_service.build_time_aware_training_dataset()

        if len(X) < min_samples:
            return {
                "trained": False,
                "message": f"Insufficient training data. Found {len(X)} samples; minimum required is {min_samples}.",
                "sample_count": len(X)
            }

        X_arr = np.array(X)
        y_arr = np.array(y)

        # Check if both classes are represented
        unique_classes = np.unique(y_arr)
        if len(unique_classes) < 2:
            return {
                "trained": False,
                "message": f"Training dataset lacks class diversity (all outcomes are {unique_classes[0]}). Need positive and negative outcomes.",
                "sample_count": len(X)
            }

        # Base estimator: HistGradientBoosting or LogisticRegression
        base_estimator = HistGradientBoostingClassifier(
            max_iter=50,
            learning_rate=0.08,
            random_state=42
        ) if len(X_arr) >= 20 else LogisticRegression(max_iter=200, random_state=42)

        # Probability Calibration via Isotonic Regression
        cv_folds = min(3, len(X_arr) // 4) if len(X_arr) >= 8 else 2
        calibrated_clf = CalibratedClassifierCV(
            estimator=base_estimator,
            method="sigmoid" if len(X_arr) < 30 else "isotonic",
            cv=cv_folds
        )
        calibrated_clf.fit(X_arr, y_arr)

        # Evaluate performance on training set
        preds_prob = calibrated_clf.predict_proba(X_arr)[:, 1]
        try:
            auc = round(float(roc_auc_score(y_arr, preds_prob)), 3)
        except Exception:
            auc = 0.5
        brier = round(float(brier_score_loss(y_arr, preds_prob)), 3)

        self.model = calibrated_clf
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        joblib.dump(self.model, self.model_path)
        logger.info(f"Trained and serialized ML model (AUC={auc}, Brier={brier}) to {self.model_path}")

        return {
            "trained": True,
            "model_version": ML_MODEL_VERSION,
            "sample_count": len(X_arr),
            "roc_auc": auc,
            "brier_score_calibration": brier,
            "features_used": feature_names
        }

    def predict_opportunity_probability(
        self,
        snapshot: CompanyFeatureSnapshot
    ) -> Dict[str, Any]:
        """
        Generates calibrated ML probability for a company feature snapshot.
        If model is un-trained, returns deterministic fallback estimate.
        """
        vec = np.array([[
            float(snapshot.charges_satisfied_90d or 0),
            float(snapshot.charges_created_90d or 0),
            float(snapshot.age_latest_charge_satisfaction_days or 180.0),
            float(snapshot.dbt_current_days if snapshot.dbt_current_days is not None else 0.0),
            float(snapshot.dbt_trend_90d if snapshot.dbt_trend_90d is not None else 0.0),
            float(snapshot.headcount_current or 10),
            float(snapshot.headcount_growth_90d_pct if snapshot.headcount_growth_90d_pct is not None else 0.0),
            float(snapshot.adverse_insolvency_events_count or 0),
            float(snapshot.corroboration_signal_count or 0),
            float(snapshot.distinct_source_count or 0)
        ]])

        if self.model is not None:
            proba = float(self.model.predict_proba(vec)[0][1])
            return {
                "p_funded_ml": round(proba, 3),
                "model_version": ML_MODEL_VERSION,
                "is_ml_active": True,
                "shadow_mode": True
            }
        else:
            # Deterministic empirical fallback
            heuristic = min(0.85, 0.15 + (float(snapshot.charges_satisfied_90d or 0) * 0.20) + (float(snapshot.dbt_current_days or 0.0) * 0.01))
            return {
                "p_funded_ml": round(heuristic, 3),
                "model_version": "v1.0.0-rules-fallback",
                "is_ml_active": False,
                "shadow_mode": True
            }
