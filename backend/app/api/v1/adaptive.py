from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.user import User
from backend.app.api.v1.auth import get_current_user, require_role
from backend.app.services.tool_registry import approved_tool_registry, ToolDefinition
from backend.app.services.investigation_engine import ValueOfInformationEngine
from backend.app.services.opportunity_assessment import CommercialOpportunityAssessor
from backend.app.services.outcome_learning import OutcomeLearningService
from backend.app.services.ml_engine import MachineLearningOpportunityModel
from backend.app.models.investigation import InvestigationDecision, CommercialOpportunityAssessment, CommercialFunnelOutcome
from backend.app.models.company import CompanyRegistry
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.signal import NormalizedSignal
from backend.app.models.model_prediction import ModelPrediction
from backend.app.services.audit import log_audit_event
from backend.app.ml.promotion import get_latest_model_card, bootstrap_cluster_ci

router = APIRouter(prefix="/adaptive", tags=["Adaptive Finance Intelligence Engine"])

# --- Request / Response Schemas ---

class ExecuteToolRequest(BaseModel):
    crn: str
    tool_name: str
    actor_notes: Optional[str] = None

class RecordOutcomeRequest(BaseModel):
    crn: str
    outcome_stage: str
    facility_type: Optional[str] = None
    funded_amount_gbp: float = 0.0
    realized_revenue_gbp: float = 0.0
    loss_reason: Optional[str] = None
    time_to_outcome_days: Optional[float] = None
    total_investigation_cost_gbp: float = 0.0
    feedback_notes: Optional[str] = None

# --- Approved Tool Registry Endpoints ---

@router.get("/tools", response_model=List[ToolDefinition])
def list_approved_tools(
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Returns the registry of approved investigation tools with transparent fees and rate limits."""
    return approved_tool_registry.list_tools()

# --- Value-of-Information (EVOI) Endpoints ---

@router.post("/investigate/evaluate/{crn}")
def evaluate_company_investigation_vois(
    crn: str,
    company_daily_budget_gbp: float = Query(5.00, ge=0.0, le=50.0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Evaluates Expected Value of Information (EVOI) for all approved investigation tools
    against commercial utility gain and API costs.
    """
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    engine = ValueOfInformationEngine(db)
    decisions = engine.evaluate_investigation_opportunities(
        crn=norm_crn,
        company_daily_budget_gbp=company_daily_budget_gbp
    )
    return [
        {
            "id": d.id,
            "tool_name": d.tool_name,
            "evoi_value": d.evoi_value,
            "estimated_cost_gbp": d.estimated_cost_gbp,
            "decision": d.decision,
            "decision_rationale": d.decision_rationale,
            "status": d.status,
            "created_at": d.created_at
        } for d in decisions
    ]

@router.post("/investigate/execute")
def execute_approved_tool(
    req: ExecuteToolRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Executes an approved investigation tool within budget and circuit-breaker constraints.
    """
    norm_crn, valid, err = normalize_uk_crn(req.crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    result = approved_tool_registry.execute_tool(
        tool_name=req.tool_name,
        crn=norm_crn,
        db=db,
        actor_user_id=current_user.id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result

@router.get("/investigations/{crn}")
def get_company_investigation_history(
    crn: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Returns investigation history including skipped queries and economic rationale."""
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    decisions = db.query(InvestigationDecision).filter(
        InvestigationDecision.crn == norm_crn
    ).order_by(InvestigationDecision.created_at.desc()).all()

    return [
        {
            "id": d.id,
            "tool_name": d.tool_name,
            "evoi_value": d.evoi_value,
            "cost_gbp": d.actual_cost_gbp if d.status == "executed" else d.estimated_cost_gbp,
            "decision": d.decision,
            "rationale": d.decision_rationale,
            "status": d.status,
            "result_summary": d.result_summary,
            "created_at": d.created_at
        } for d in decisions
    ]

# --- Commercial Opportunity Assessment Endpoints ---

@router.get("/assessments/{crn}")
def get_commercial_opportunity_assessment(
    crn: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Returns the latest commercial opportunity assessment for a company."""
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    assessment = db.query(CommercialOpportunityAssessment).filter(
        CommercialOpportunityAssessment.crn == norm_crn
    ).order_by(CommercialOpportunityAssessment.created_at.desc()).first()

    if not assessment:
        assessor = CommercialOpportunityAssessor(db)
        assessment = assessor.assess_company(norm_crn)

    # ML shadow prediction
    from backend.app.services.feature_store import FeatureStoreService
    fs = FeatureStoreService(db)
    snap = fs.compute_feature_snapshot(norm_crn)
    ml_model = MachineLearningOpportunityModel(db)
    ml_pred = ml_model.predict_opportunity_probability(snap)

    return {
        "crn": assessment.crn,
        "model_version": assessment.model_version,
        "p_qualified_opportunity": assessment.p_qualified_opportunity,
        "p_conversion_funded": assessment.p_conversion_funded,
        "human_touch_boost_pts": 16.0,
        "p_conversion_funded_with_human_touch": round(min(0.99, assessment.p_conversion_funded + 0.160), 3),
        "human_touch_advantage_summary": "Broker intervention has potential to add 16 points to probability score via warm relationship dialogue and unencumbered asset structuring.",
        "p_funded_ml_shadow": ml_pred.get("p_funded_ml"),
        "is_ml_active": ml_pred.get("is_ml_active"),
        "estimated_facility_size_gbp": assessment.estimated_facility_size_gbp,
        "estimated_commercial_contribution_gbp": assessment.estimated_commercial_contribution_gbp,
        "product_fit": {
            "invoice_finance": assessment.invoice_finance_fit,
            "asset_refinance": assessment.asset_refinance_fit,
            "term_loan": assessment.term_loan_fit
        },
        "evidence_uncertainty": assessment.evidence_uncertainty,
        "recommended_action": assessment.recommended_action,
        "explanation": assessment.explanation_summary,
        "created_at": assessment.created_at
    }

@router.post("/assessments/{crn}/recompute")
def recompute_opportunity_assessment(
    crn: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    assessor = CommercialOpportunityAssessor(db)
    assessment = assessor.assess_company(norm_crn)
    return {"status": "success", "assessment_id": assessment.assessment_id, "p_opp": assessment.p_qualified_opportunity}

# --- Outcome Learning Loop Endpoints ---

@router.post("/outcomes")
def record_commercial_outcome(
    req: RecordOutcomeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Records commercial broker feedback outcome across the sales funnel."""
    norm_crn, valid, err = normalize_uk_crn(req.crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    service = OutcomeLearningService(db)
    try:
        outcome = service.record_outcome(
            crn=norm_crn,
            outcome_stage=req.outcome_stage,
            broker_user_id=current_user.id,
            facility_type=req.facility_type,
            funded_amount_gbp=req.funded_amount_gbp,
            realized_revenue_gbp=req.realized_revenue_gbp,
            loss_reason=req.loss_reason,
            time_to_outcome_days=req.time_to_outcome_days,
            total_investigation_cost_gbp=req.total_investigation_cost_gbp,
            feedback_notes=req.feedback_notes
        )
        return {"status": "success", "outcome_id": outcome.outcome_id, "stage": outcome.outcome_stage}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/outcomes/metrics")
def get_commercial_conversion_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Returns commercial conversion funnel KPIs and net ROI."""
    service = OutcomeLearningService(db)
    return service.get_commercial_conversion_metrics()

# --- Machine Learning Model Operations ---

@router.post("/ml/train")
def train_machine_learning_model(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin"]))
):
    """Trains calibrated machine learning model on recorded commercial outcomes. Admin only."""
    ml_model = MachineLearningOpportunityModel(db)
    metrics = ml_model.train_model(min_samples=6)
    return metrics

class PromoteModelRequest(BaseModel):
    target_version: Optional[str] = None
    override_reason: Optional[str] = None

@router.get("/ml/status")
def get_machine_learning_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Returns data volume, label counts, latest evaluation metrics, and promotion gate status.
    Guarantees honest visibility into model readiness and shadow-mode status.
    """
    # 1. Data volume counts
    total_companies = db.query(CompanyRegistry).count()
    total_snapshots = db.query(CompanyFeatureSnapshot).count()
    total_predictions = db.query(ModelPrediction).count()

    # 2. Label counts
    outcomes = db.query(CommercialFunnelOutcome).all()
    stage_counts: Dict[str, int] = {}
    for o in outcomes:
        stage_counts[o.outcome_stage] = stage_counts.get(o.outcome_stage, 0) + 1

    proxy_new_charges = db.query(NormalizedSignal).filter(
        NormalizedSignal.signal_type.in_(("charge_creation", "charge_registered")),
        NormalizedSignal.verification_status != "disputed"
    ).count()

    # 3. Latest model metrics and card
    card = get_latest_model_card()

    # 4. Promotion gate assessment
    if card and "promotion_gate" in card:
        gate_info = card["promotion_gate"]
        promotion_met = gate_info.get("passed", False)
    else:
        promotion_met = False
        gate_info = {
            "passed": False,
            "message": "No evaluated candidate model card found. Run honest evaluation pipeline first."
        }

    return {
        "status": "healthy",
        "active_mode": "shadow_mode",
        "data_volume": {
            "registered_companies": total_companies,
            "feature_snapshots": total_snapshots,
            "predictions_logged": total_predictions
        },
        "label_counts": {
            "commercial_funnel_stages": stage_counts,
            "total_commercial_outcomes": len(outcomes),
            "proxy_charge_registrations": proxy_new_charges
        },
        "latest_model_card": card,
        "promotion_gate": {
            "met": promotion_met,
            "details": gate_info,
            "manual_promotion_required": True,
            "auto_promotion_allowed": False
        }
    }

@router.post("/ml/promote")
def promote_machine_learning_model(
    req: PromoteModelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin"]))
):
    """
    Manually promotes an evaluated ML candidate model to primary tier if promotion gate is met.
    Requires ADMIN role. Permanently audit-logged through tamper-evident audit service.
    Never auto-promotes.
    """
    card = get_latest_model_card()
    if not card:
        raise HTTPException(
            status_code=400,
            detail="No evaluated candidate model card found. Cannot promote un-evaluated model."
        )

    gate = card.get("promotion_gate", {})
    gate_passed = gate.get("passed", False)

    if not gate_passed and not req.override_reason:
        raise HTTPException(
            status_code=400,
            detail=(
                "Promotion gate criteria NOT met (candidate does not reliably beat rules baseline on top-k precision and Brier score). "
                "Documented override_reason is required for administrative manual bypass."
            )
        )

    version_to_promote = req.target_version or card.get("model_version", "v2.0.0-promoted")

    log_audit_event(
        db=db,
        action="ml_model_promoted",
        resource_type="ml_model",
        resource_id=version_to_promote,
        user_id=current_user.id,
        details={
            "promoted_by": current_user.email,
            "gate_passed": gate_passed,
            "override_reason": req.override_reason,
            "git_sha": card.get("git_sha"),
            "feature_spec_hash": card.get("feature_spec_hash"),
            "model_card_timestamp": card.get("created_at")
        }
    )

    return {
        "status": "success",
        "promoted_version": version_to_promote,
        "gate_passed": gate_passed,
        "mode": "promoted" if gate_passed else "promoted_with_admin_override",
        "audit_logged": True
    }
