import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.review import BrokerReviewAction
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.models.signal import NormalizedSignal
from backend.app.models.user import User, AuditLog
from backend.app.api.v1.auth import require_role
from backend.app.schemas.review import ReviewActionCreate, ReviewActionOut
from backend.app.schemas.company import CompanyMatchReviewOut, MatchReviewDecision

from backend.app.services.audit import log_audit_event

router = APIRouter(prefix="/reviews", tags=["Broker Review & Resolution Workflow"])

@router.post("/prospects/{crn}", response_model=ReviewActionOut)
def record_prospect_review_action(
    crn: str,
    action_in: ReviewActionCreate,
    db: Session = Depends(get_db)
):
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    company = db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    now = datetime.now(timezone.utc)
    snooze_until = None
    if action_in.snooze_days:
        snooze_until = now + timedelta(days=action_in.snooze_days)

    # Handle signal dispute if specified
    if action_in.signal_id_disputed:
        sig = db.query(NormalizedSignal).filter(
            NormalizedSignal.signal_id == action_in.signal_id_disputed,
            NormalizedSignal.crn == norm_crn
        ).first()
        if sig:
            sig.verification_status = "disputed"
            db.commit()

    review_action = BrokerReviewAction(
        crn=norm_crn,
        assigned_broker_name=action_in.assigned_broker_name,
        review_status=action_in.review_status,
        signal_id_disputed=action_in.signal_id_disputed,
        dispute_reason=action_in.dispute_reason,
        notes=action_in.notes,
        verification_request=action_in.verification_request,
        snooze_until=snooze_until,
        outcome=action_in.outcome,
        outcome_notes=action_in.outcome_notes,
        created_at=now,
        updated_at=now
    )
    db.add(review_action)
    db.commit()
    db.refresh(review_action)
    
    # Tamper-evident Audit log entry with hash chaining
    log_audit_event(
        db=db,
        action=f"broker_review_{action_in.review_status}",
        resource_type="prospect",
        resource_id=norm_crn,
        details={
            "status": action_in.review_status,
            "broker": action_in.assigned_broker_name,
            "disputed_signal": action_in.signal_id_disputed,
            "outcome": action_in.outcome
        }
    )
    return review_action

@router.get("/prospects/{crn}/history", response_model=List[ReviewActionOut])
def get_review_history_for_company(crn: str, db: Session = Depends(get_db)):
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail="Invalid CRN")
    return db.query(BrokerReviewAction).filter(BrokerReviewAction.crn == norm_crn).order_by(BrokerReviewAction.created_at.desc()).all()

# --- Entity Match Review Queue Endpoints ---

@router.get("/matches", response_model=List[CompanyMatchReviewOut])
def list_pending_entity_matches(status: str = "pending", db: Session = Depends(get_db)):
    return db.query(CompanyMatchReview).filter(CompanyMatchReview.status == status).order_by(CompanyMatchReview.created_at.desc()).all()

@router.post("/matches/{match_id}/decision", response_model=CompanyMatchReviewOut)
def decide_entity_match(
    match_id: int,
    decision: MatchReviewDecision,
    db: Session = Depends(get_db)
):
    review = db.query(CompanyMatchReview).filter(CompanyMatchReview.id == match_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Match review entry not found")

    now = datetime.now(timezone.utc)
    review.status = decision.status
    review.reviewer_notes = decision.reviewer_notes
    review.reviewed_at = now
    
    # Tamper-evident Audit log entry with hash chaining
    log_audit_event(
        db=db,
        action=f"entity_match_{decision.status}",
        resource_type="company_match",
        resource_id=str(match_id),
        details={"source_identifier": review.source_identifier, "candidate_crn": review.candidate_crn}
    )
    return review

class UnmergeRequest(BaseModel):
    source_identifier: str
    current_crn: str
    target_crn: Optional[str] = None
    reason: str

@router.post("/unmerge")
def unmerge_entity_matches(
    req: UnmergeRequest,
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst"])),
    db: Session = Depends(get_db)
):
    """
    Splits or unmerges an incorrect entity resolution match.
    Restricted to Admin and Analyst roles.
    """
    from backend.app.services.resolution import EntityResolutionService
    service = EntityResolutionService(db)
    success, msg = service.unmerge_or_split_entity(
        source_identifier=req.source_identifier,
        current_crn=req.current_crn,
        target_crn=req.target_crn,
        reason=req.reason
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    log_audit_event(
        db=db,
        action="entity_unmerge_split",
        resource_type="entity_resolution",
        resource_id=req.source_identifier,
        user_id=current_user.id,
        user_email=current_user.email,
        user_role=current_user.role,
        details={"current_crn": req.current_crn, "target_crn": req.target_crn, "reason": req.reason}
    )
    return {"status": "success", "message": msg}
