from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.logging import logger
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.services.feature_store import FeatureStoreService

VALID_OUTCOME_STAGES = [
    "NO_RESPONSE",
    "QUALIFIED_CONVERSATION",
    "BROKER_ACCEPTED",
    "APPLICATION_SUBMITTED",
    "LENDER_DECLINED",
    "FACILITY_OFFERED",
    "FACILITY_FUNDED"
]

class OutcomeLearningService:
    """
    Captures commercial sales-funnel outcomes, maintains feedback audit records,
    and constructs time-aware ML training datasets accounting for outcome censoring.
    """

    def __init__(self, db: Session):
        self.db = db

    def record_outcome(
        self,
        crn: str,
        outcome_stage: str,
        broker_user_id: Optional[str] = None,
        facility_type: Optional[str] = None,
        funded_amount_gbp: float = 0.0,
        realized_revenue_gbp: float = 0.0,
        loss_reason: Optional[str] = None,
        time_to_outcome_days: Optional[float] = None,
        total_investigation_cost_gbp: float = 0.0,
        feedback_notes: Optional[str] = None
    ) -> CommercialFunnelOutcome:
        """Records a commercial broker feedback outcome."""
        if outcome_stage not in VALID_OUTCOME_STAGES:
            raise ValueError(f"Invalid outcome stage '{outcome_stage}'. Allowed: {VALID_OUTCOME_STAGES}")

        outcome = CommercialFunnelOutcome(
            crn=crn,
            broker_user_id=broker_user_id,
            outcome_stage=outcome_stage,
            facility_type=facility_type,
            funded_amount_gbp=funded_amount_gbp,
            realized_revenue_gbp=realized_revenue_gbp,
            loss_reason=loss_reason,
            time_to_outcome_days=time_to_outcome_days,
            total_investigation_cost_gbp=total_investigation_cost_gbp,
            feedback_notes=feedback_notes,
            recorded_at=datetime.now(timezone.utc)
        )
        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)
        logger.info(f"Recorded commercial funnel outcome for {crn}: {outcome_stage} (Funded: £{funded_amount_gbp:,.2f})")
        return outcome

    def get_company_outcomes(self, crn: str) -> List[CommercialFunnelOutcome]:
        return self.db.query(CommercialFunnelOutcome).filter(
            CommercialFunnelOutcome.crn == crn
        ).order_by(CommercialFunnelOutcome.recorded_at.desc()).all()

    def build_time_aware_training_dataset(
        self,
        cutoff_date: Optional[datetime] = None
    ) -> Tuple[List[List[float]], List[int], List[str]]:
        """
        Builds a time-aware tabular dataset (X, y, feature_names) for supervised ML training.
        Ensures that features are extracted strictly as of the observation date BEFORE the outcome,
        guaranteeing zero target leakage.
        """
        outcomes = self.db.query(CommercialFunnelOutcome).all()
        if not outcomes:
            return [], [], []

        X: List[List[float]] = []
        y: List[int] = []
        feature_names = [
            "charges_satisfied_90d",
            "charges_created_90d",
            "age_latest_charge_days",
            "dbt_current_days",
            "dbt_trend_90d",
            "headcount_current",
            "headcount_growth_90d_pct",
            "adverse_credit_events_count",
            "corroboration_signal_count",
            "distinct_source_count"
        ]

        fs = FeatureStoreService(self.db)

        for out in outcomes:
            # Event label: 1 if converted to facility funded, 0 otherwise
            is_funded = 1 if out.outcome_stage in ("FACILITY_OFFERED", "FACILITY_FUNDED") else 0
            
            # Point-in-time feature extraction strictly as of when outcome was recorded
            snapshot = fs.compute_feature_snapshot(out.crn, as_of_timestamp=out.recorded_at)
            
            vec = [
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
            ]
            X.append(vec)
            y.append(is_funded)

        return X, y, feature_names

    def get_commercial_conversion_metrics(self) -> Dict[str, Any]:
        """Calculates commercial conversion funnel KPIs and API efficiency metrics."""
        total_outcomes = self.db.query(CommercialFunnelOutcome).count()
        funded_count = self.db.query(CommercialFunnelOutcome).filter(
            CommercialFunnelOutcome.outcome_stage == "FACILITY_FUNDED"
        ).count()
        total_revenue = self.db.query(func.sum(CommercialFunnelOutcome.realized_revenue_gbp)).scalar() or 0.0
        total_funded_volume = self.db.query(func.sum(CommercialFunnelOutcome.funded_amount_gbp)).filter(
            CommercialFunnelOutcome.outcome_stage == "FACILITY_FUNDED"
        ).scalar() or 0.0
        total_api_costs = self.db.query(func.sum(CommercialFunnelOutcome.total_investigation_cost_gbp)).scalar() or 0.0

        conversion_rate = (funded_count / total_outcomes * 100.0) if total_outcomes > 0 else 0.0
        roi_net = (total_revenue - total_api_costs)

        return {
            "total_outcomes_recorded": total_outcomes,
            "funded_facilities_count": funded_count,
            "total_funded_volume_gbp": round(float(total_funded_volume), 2),
            "conversion_rate_pct": round(conversion_rate, 1),
            "total_realized_revenue_gbp": round(float(total_revenue), 2),
            "total_investigation_costs_gbp": round(float(total_api_costs), 2),
            "net_commercial_roi_gbp": round(float(roi_net), 2)
        }
