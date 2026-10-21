import math
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.investigation import CommercialOpportunityAssessment
from backend.app.models.model_prediction import ModelPrediction
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.ml_engine import MachineLearningOpportunityModel
from backend.app.ml.features import extract_features_dict

COMMERCIAL_MODEL_VERSION = "v1.0.0-rules"

class CommercialOpportunityAssessor:
    """
    Assesses commercial funding opportunities, converting technical strain features
    into calibrated probabilities of finance demand, conversion to a funded facility,
    broker commission projections, and specific finance product suitability.
    """

    def __init__(self, db: Session):
        self.db = db

    def assess_company(
        self,
        crn: str,
        as_of_timestamp: Optional[datetime] = None
    ) -> CommercialOpportunityAssessment:
        if as_of_timestamp is None:
            as_of_timestamp = datetime.now(timezone.utc)

        fs = FeatureStoreService(self.db)
        snapshot = fs.compute_feature_snapshot(crn, as_of_timestamp=as_of_timestamp)

        # 1. P(Qualified Finance Opportunity)
        # Driven by charge satisfaction recency, DBT stretch, and payroll expansion
        strain_pts = 0.0
        
        # Financing pillar
        if (snapshot.charges_satisfied_90d or 0) > 0:
            age = snapshot.age_latest_charge_satisfaction_days or 30.0
            recency = math.exp(-0.693 * (age / 60.0))
            scope_mult = 1.0 if snapshot.latest_charge_scope == "full" else 0.5
            strain_pts += 35.0 * recency * scope_mult

        # Credit pillar
        if snapshot.dbt_current_days is not None:
            dbt = snapshot.dbt_current_days
            if dbt >= 25.0:
                strain_pts += 35.0
            elif dbt >= 15.0:
                strain_pts += 22.0
            elif dbt >= 5.0:
                strain_pts += 10.0

        # Workforce expansion
        if snapshot.headcount_growth_90d_pct and snapshot.headcount_growth_90d_pct > 15.0:
            strain_pts += min(20.0, snapshot.headcount_growth_90d_pct * 0.8)

        # Logistic calibration for P(Opportunity)
        p_opp = 1.0 / (1.0 + math.exp(-0.06 * (strain_pts - 40.0)))
        p_opp = round(max(0.05, min(0.95, p_opp)), 3)

        # 2. P(Conversion to Funded Facility)
        # Accounts for credit hygiene: high adverse judgments or severe delinquent accounts decrease conversion
        hygiene_discount = 1.0
        if (snapshot.adverse_insolvency_events_count or 0) > 2:
            hygiene_discount = 0.60
        elif (snapshot.adverse_insolvency_events_count or 0) == 1:
            hygiene_discount = 0.85

        p_funded = round(p_opp * 0.45 * hygiene_discount, 3)

        # 3. Estimated Facility Size & Commercial Contribution (£)
        # Based on company scale (headcount, DBT exposure)
        hc = snapshot.headcount_current or 15
        estimated_facility_size = max(50_000.0, float(hc) * 12_500.0)
        # Standard commercial finance broker commission: ~2.0% of facility limit
        estimated_contribution = round(estimated_facility_size * 0.02, 2)

        # 4. Product Suitability Matrix (0 - 100)
        # Invoice Finance: High when DBT is elevated (slow customer payment cycle / supplier stretch)
        inv_fit = 0.0
        if snapshot.dbt_current_days and snapshot.dbt_current_days >= 10.0:
            inv_fit = min(95.0, 40.0 + (snapshot.dbt_current_days * 2.0))
        if snapshot.charges_satisfied_90d:
            inv_fit = min(95.0, inv_fit + 20.0)

        # Asset Refinance: High when full charge satisfaction occurred (unencumbered assets freed)
        asset_fit = 0.0
        if (snapshot.charges_satisfied_90d or 0) > 0 and snapshot.latest_charge_scope == "full":
            asset_fit = 85.0
        elif (snapshot.charges_satisfied_180d or 0) > 0:
            asset_fit = 60.0

        # Term Loan: High when growing rapidly with moderate DBT
        loan_fit = 0.0
        if snapshot.headcount_growth_90d_pct and snapshot.headcount_growth_90d_pct > 10.0:
            loan_fit = min(90.0, 50.0 + snapshot.headcount_growth_90d_pct)
        if (snapshot.dbt_current_days or 0) < 15.0 and loan_fit > 0:
            loan_fit = min(90.0, loan_fit + 15.0)

        # 5. Model Uncertainty & Missing Information Index
        missing_count = 0
        if snapshot.dbt_current_days is None:
            missing_count += 1
        if snapshot.headcount_growth_90d_pct is None:
            missing_count += 1
        if snapshot.credit_score_drop_90d is None:
            missing_count += 1
        uncertainty = round(missing_count / 3.0, 2)

        # 6. Recommended Commercial Action
        if p_opp >= 0.70 and p_funded >= 0.25:
            action = "PRIORITY_DIRECT_OUTREACH: Engage Managing Director / Financial Controller"
        elif p_opp >= 0.50:
            action = "PRE_QUALIFICATION: Conduct Director Filings & Balance Sheet Review"
        elif uncertainty >= 0.60:
            action = "INVESTIGATE_GAPS: Execute EVOI data refresh on credit & workforce metrics"
        else:
            action = "MONITOR: Retain on 30-day automated Companies House watch list"

        summary = (
            f"Evaluated P(Opportunity)={p_opp*100:.1f}%, P(Funded)={p_funded*100:.1f}%. "
            f"Estimated Facility Size: £{estimated_facility_size:,.0f} (£{estimated_contribution:,.0f} est. commission). "
            f"Top Product Fit: {'Invoice Discounting' if inv_fit >= max(asset_fit, loan_fit) else ('Asset Refinance' if asset_fit >= loan_fit else 'Commercial Term Loan')}."
        )

        assessment = CommercialOpportunityAssessment(
            crn=crn,
            model_version=COMMERCIAL_MODEL_VERSION,
            assessment_type="rules_baseline",
            p_qualified_opportunity=p_opp,
            p_conversion_funded=p_funded,
            estimated_facility_size_gbp=estimated_facility_size,
            estimated_commercial_contribution_gbp=estimated_contribution,
            invoice_finance_fit=round(inv_fit, 1),
            asset_refinance_fit=round(asset_fit, 1),
            term_loan_fit=round(loan_fit, 1),
            evidence_uncertainty=uncertainty,
            recommended_action=action,
            explanation_summary=summary,
            created_at=as_of_timestamp
        )
        self.db.add(assessment)
        self.db.commit()
        self.db.refresh(assessment)

        # Persist model prediction record for shadow tracking & honest verification
        try:
            ml_engine = MachineLearningOpportunityModel(self.db)
            ml_pred = ml_engine.predict_opportunity_probability(snapshot)
            f_dict = extract_features_dict(snapshot)

            pred_record = ModelPrediction(
                crn=crn,
                as_of=as_of_timestamp,
                model_version=ml_pred.get("model_version", "v1.1.0-ml-shadow"),
                p_value=float(ml_pred.get("p_funded_ml", p_funded)),
                rules_p_value=float(p_funded),
                feature_vector_json=json.dumps(f_dict)
            )
            self.db.add(pred_record)
            self.db.commit()
        except Exception as e:
            logger.warning(f"Could not log ModelPrediction shadow record for {crn}: {e}")

        return assessment
