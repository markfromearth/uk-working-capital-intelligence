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
    PersonIdentity,
    CompanyOfficer,
    ProfessionalRoleProfile,
    SuppressionRecord
)
from backend.app.services.decision_maker_discovery import DecisionMakerDiscoveryService
from backend.app.services.identity_resolution import IdentityResolutionService
from backend.app.services.decision_maker_validation import DecisionMakerValidationGate, ValidationGateResult

router = APIRouter(prefix="/decision-makers", tags=["Decision-Maker Intelligence"])

# --- Request / Response Schemas ---

class ManualVerificationRequest(BaseModel):
    evidence_source: str = Field(..., description="Corroborating evidence source, e.g. corporate filing reference or direct verification")
    notes: Optional[str] = None

class ErasureRequest(BaseModel):
    reason: str = Field(default="GDPR Article 17 Right to Erasure")

class SuppressionRequest(BaseModel):
    identifier_type: str = Field(..., description="crn, person_id, full_name_crn, email_domain, phone")
    identifier_value: str
    suppression_reason: str = Field(..., description="gdpr_article_21_objection, gdpr_article_17_erasure, ctps_opt_out, broker_do_not_contact")
    notes: Optional[str] = None

# --- Endpoints ---

@router.get("/company/{crn}/officers")
def get_company_officers(
    crn: str,
    db: Session = Depends(get_db)
):
    """Returns statutory officers and PSC register for a canonical company."""
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    officers = db.query(CompanyOfficer).filter(CompanyOfficer.crn == norm_crn).all()
    return [
        {
            "id": o.id,
            "officer_name": o.officer_name,
            "officer_role": o.officer_role,
            "relationship_category": o.relationship_category,
            "appointed_on": o.appointed_on,
            "resigned_on": o.resigned_on,
            "notified_on": o.notified_on,
            "ceased_on": o.ceased_on,
            "is_active": o.is_active,
            "occupation": o.occupation,
            "nationality": o.nationality,
            "ownership_percentage_lower": o.ownership_percentage_lower,
            "ownership_percentage_upper": o.ownership_percentage_upper,
            "source_system": o.source_system,
            "person_id": o.person_id
        } for o in officers
    ]

@router.get("/company/{crn}/profiles")
def get_company_decision_maker_profiles(
    crn: str,
    db: Session = Depends(get_db)
):
    """
    Returns mapped professional profiles with 5-dimension validation gate results.
    Guarantees strict separation between company opportunity and identity validation.
    """
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    profiles = db.query(ProfessionalRoleProfile).filter(
        ProfessionalRoleProfile.crn == norm_crn
    ).all()

    gate = DecisionMakerValidationGate(db)
    results = []

    for p in profiles:
        gate_res = gate.evaluate_decision_maker_gates(norm_crn, p.id)
        results.append({
            "profile_id": p.id,
            "person_id": p.person_id,
            "person_name": p.person.full_name if not p.person.is_erased else "[REDACTED_ERASURE]",
            "role_title": p.role_title,
            "standardized_role": p.standardized_role,
            "seniority_level": p.seniority_level,
            "is_finance_decision_maker": p.is_finance_decision_maker,
            "is_current": p.is_current,
            "platform": p.platform,
            "match_confidence": p.match_confidence,
            "verification_status": p.verification_status,
            "is_stale": p.is_stale,
            "validation_gate": gate_res.dict()
        })

    return results

@router.post("/company/{crn}/discover")
def trigger_decision_maker_discovery(
    crn: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Discovers officers and PSCs from official registry and maps decision-makers."""
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    service = DecisionMakerDiscoveryService(db)
    try:
        res = service.discover_company_personnel(norm_crn, actor_user_id=current_user.id)
        return {"status": "success", "discovery": res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/profiles/{profile_id}/resolve")
def resolve_profile_verification(
    profile_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Executes multi-signal identity resolution. Names alone cannot validate."""
    resolver = IdentityResolutionService(db)
    try:
        updated = resolver.resolve_profile_verification(profile_id, actor_user_id=current_user.id)
        return {
            "profile_id": updated.id,
            "status": updated.verification_status,
            "confidence": updated.match_confidence,
            "is_stale": updated.is_stale
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/profiles/{profile_id}/verify-manual")
def manually_verify_profile(
    profile_id: str,
    req: ManualVerificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Authorized manual validation with documented evidence citation."""
    resolver = IdentityResolutionService(db)
    try:
        prof = resolver.manually_verify_profile(
            profile_id=profile_id,
            verified_by_user_id=current_user.id,
            corroborating_evidence_source=req.evidence_source,
            notes=req.notes
        )
        return {"status": "success", "verification_status": prof.verification_status, "confidence": prof.match_confidence}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/persons/{person_id}/erasure")
def execute_right_to_erasure(
    person_id: str,
    req: ErasureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin"]))
):
    """Executes GDPR Article 17 Right to Erasure. Admin role required."""
    resolver = IdentityResolutionService(db)
    try:
        res = resolver.execute_right_to_erasure(
            person_id=person_id,
            requested_by_user_id=current_user.id,
            reason=req.reason
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/suppression")
def record_suppression(
    req: SuppressionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """Records an objection or opt-out in the central suppression registry."""
    resolver = IdentityResolutionService(db)
    supp = resolver.record_suppression_objection(
        identifier_type=req.identifier_type,
        identifier_value=req.identifier_value,
        suppression_reason=req.suppression_reason,
        recorded_by_user_id=current_user.id,
        notes=req.notes
    )
    return {"status": "success", "id": supp.id, "identifier": supp.identifier_value, "reason": supp.suppression_reason}

@router.get("/suppression")
def list_suppressions(db: Session = Depends(get_db)):
    """Lists active suppression entries."""
    records = db.query(SuppressionRecord).filter(SuppressionRecord.is_active == True).all()
    return [
        {
            "id": r.id,
            "type": r.identifier_type,
            "value": r.identifier_value,
            "reason": r.suppression_reason,
            "recorded_at": r.recorded_at,
            "notes": r.notes
        } for r in records
    ]
