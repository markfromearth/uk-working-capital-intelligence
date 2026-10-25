from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.user import User
from backend.app.api.v1.auth import get_current_user, require_role
from backend.app.models.decision_maker import (
    AudienceActivation,
    CampaignPerformanceMetric
)
from backend.app.services.audience_activation import AudienceActivationService

router = APIRouter(prefix="/audiences", tags=["Audience Activation & Campaign Measurement"])

# --- Request / Response Schemas ---

class CreateActivationRequest(BaseModel):
    campaign_name: str
    crn: str
    destination_platform: str = Field(..., description="linkedin_company_targeting, platform_abm_native, direct_broker_outreach, hubspot_first_party")
    activation_type: str = Field(..., description="company_level_account, platform_native_criteria, broker_direct_outreach, first_party_crm")
    purpose_specification: str
    lawful_basis: str = Field(default="legitimate_interests", description="legitimate_interests, consent, contract")
    legitimate_interest_assessment_ref: Optional[str] = None
    professional_profile_id: Optional[str] = None
    retention_days: int = Field(default=90, ge=30, le=365)

class ComplianceReviewRequest(BaseModel):
    approve: bool
    review_notes: str

class RecordPerformanceRequest(BaseModel):
    spend_gbp: float = 0.0
    impressions_count: int = 0
    clicks_count: int = 0
    engagements_count: int = 0
    inquiries_received: int = 0
    qualified_conversations: int = 0
    facilities_funded_count: int = 0
    funded_volume_gbp: float = 0.0

# --- Endpoints ---

@router.get("/")
def list_audience_activations(
    crn: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Lists audience activations with compliance and execution statuses."""
    query = db.query(AudienceActivation)
    if (current_user.role or "").upper() not in ("ADMIN", "ANALYST"):
        query = query.filter(AudienceActivation.requester_user_id == current_user.id)
    if crn:
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if valid and norm_crn:
            query = query.filter(AudienceActivation.crn == norm_crn)
    if status_filter:
        query = query.filter(AudienceActivation.execution_status == status_filter)

    activations = query.order_by(AudienceActivation.created_at.desc()).all()
    return [
        {
            "activation_id": a.activation_id,
            "campaign_name": a.campaign_name,
            "crn": a.crn,
            "destination_platform": a.destination_platform,
            "activation_type": a.activation_type,
            "targeting_scope": a.targeting_scope,
            "purpose_specification": a.purpose_specification,
            "lawful_basis": a.lawful_basis,
            "compliance_status": a.compliance_approval_status,
            "execution_status": a.execution_status,
            "suppression_cleared": a.suppression_cleared,
            "requester_user_id": a.requester_user_id,
            "created_at": a.created_at
        } for a in activations
    ]

@router.get("/{activation_id}")
def get_activation_detail(
    activation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Returns single activation detail with audit history and performance metrics."""
    a = db.query(AudienceActivation).filter(AudienceActivation.activation_id == activation_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Activation not found.")
    if (
        (current_user.role or "").upper() not in ("ADMIN", "ANALYST")
        and a.requester_user_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied: You do not own this audience activation.")

    metrics = [
        {
            "observation_date": m.observation_date,
            "spend_gbp": m.spend_gbp,
            "impressions": m.impressions_count,
            "clicks": m.clicks_count,
            "engagements": m.engagements_count,
            "qualified_conversations": m.qualified_conversations,
            "facilities_funded": m.facilities_funded_count,
            "funded_volume_gbp": m.funded_volume_gbp
        } for m in a.metrics
    ]

    return {
        "activation_id": a.activation_id,
        "campaign_name": a.campaign_name,
        "crn": a.crn,
        "destination_platform": a.destination_platform,
        "targeting_scope": a.targeting_scope,
        "compliance_approval_status": a.compliance_approval_status,
        "compliance_review_notes": a.compliance_review_notes,
        "execution_status": a.execution_status,
        "lawful_basis": a.lawful_basis,
        "lia_ref": a.legitimate_interest_assessment_ref,
        "retention_schedule_days": a.retention_schedule_days,
        "suppression_cleared": a.suppression_cleared,
        "metrics": metrics,
        "created_at": a.created_at
    }

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_audience_activation(
    req: CreateActivationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Submits an audience activation proposal for compliance review.
    Fails closed if attempting prohibited financial distress targeting.
    """
    norm_crn, valid, err = normalize_uk_crn(req.crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    service = AudienceActivationService(db)
    try:
        activation = service.create_activation_request(
            campaign_name=req.campaign_name,
            crn=norm_crn,
            destination_platform=req.destination_platform,
            activation_type=req.activation_type,
            purpose_specification=req.purpose_specification,
            lawful_basis=req.lawful_basis,
            legitimate_interest_assessment_ref=req.legitimate_interest_assessment_ref,
            professional_profile_id=req.professional_profile_id,
            retention_days=req.retention_days,
            requester_user_id=current_user.id
        )
        return {
            "status": "pending_compliance_review",
            "activation_id": activation.activation_id,
            "campaign_name": activation.campaign_name,
            "targeting_scope": activation.targeting_scope
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{activation_id}/review")
def review_activation(
    activation_id: str,
    req: ComplianceReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin"]))
):
    """Documented compliance review and approval. Admin role required."""
    service = AudienceActivationService(db)
    try:
        updated = service.review_and_approve_activation(
            activation_id=activation_id,
            reviewer_user_id=current_user.id,
            approve=req.approve,
            review_notes=req.review_notes
        )
        return {
            "activation_id": updated.activation_id,
            "compliance_status": updated.compliance_approval_status,
            "suppression_cleared": updated.suppression_cleared,
            "notes": updated.compliance_review_notes
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{activation_id}/execute")
def execute_activation(
    activation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Executes approved audience activation against destination.
    Fail-closed: blocks execution if compliance unapproved or target suppressed.
    """
    a = db.query(AudienceActivation).filter(AudienceActivation.activation_id == activation_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Activation not found.")
    if (
        (current_user.role or "").upper() not in ("ADMIN", "ANALYST")
        and a.requester_user_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied: You do not own this audience activation.")

    service = AudienceActivationService(db)
    try:
        res = service.execute_activation(activation_id, actor_user_id=current_user.id)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{activation_id}/metrics")
def record_campaign_performance(
    activation_id: str,
    req: RecordPerformanceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Records campaign performance and qualified outcomes."""
    a = db.query(AudienceActivation).filter(AudienceActivation.activation_id == activation_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Activation not found.")
    if (
        (current_user.role or "").upper() not in ("ADMIN", "ANALYST")
        and a.requester_user_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied: You do not own this audience activation.")

    service = AudienceActivationService(db)
    try:
        metric = service.record_campaign_performance(
            activation_id=activation_id,
            spend_gbp=req.spend_gbp,
            impressions_count=req.impressions_count,
            clicks_count=req.clicks_count,
            engagements_count=req.engagements_count,
            inquiries_received=req.inquiries_received,
            qualified_conversations=req.qualified_conversations,
            facilities_funded_count=req.facilities_funded_count,
            funded_volume_gbp=req.funded_volume_gbp
        )
        return {
            "status": "success",
            "metric_id": metric.id,
            "spend_gbp": metric.spend_gbp,
            "impressions": metric.impressions_count,
            "facilities_funded": metric.facilities_funded_count
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
