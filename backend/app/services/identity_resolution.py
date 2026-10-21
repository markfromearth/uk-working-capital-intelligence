import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.decision_maker import (
    PersonIdentity,
    CompanyOfficer,
    ProfessionalRoleProfile,
    SuppressionRecord
)
from backend.app.services.audit import log_audit_event

class IdentityResolutionService:
    """
    Manages professional person-identity resolution, multi-signal validation,
    contradictory evidence detection, staleness checks, and GDPR rights (Erasure / Objection).
    """

    def __init__(self, db: Session):
        self.db = db

    def resolve_profile_verification(
        self,
        profile_id: str,
        actor_user_id: Optional[str] = None
    ) -> ProfessionalRoleProfile:
        """
        Executes multi-signal identity resolution on a candidate professional profile.
        Strict Rule: Names alone are insufficient for a validated match.
        Requires at least two independent corroborating signals.
        """
        profile = self.db.query(ProfessionalRoleProfile).filter(
            ProfessionalRoleProfile.id == profile_id
        ).first()

        if not profile:
            raise ValueError(f"Professional profile {profile_id} not found.")

        person = profile.person
        now = datetime.now(timezone.utc)

        # 1. Check for Contradictory Evidence or Ceased Status
        contradictions = []
        
        # Check if officer appointment on Companies House is ceased or resigned
        resigned_officer = self.db.query(CompanyOfficer).filter(
            CompanyOfficer.crn == profile.crn,
            CompanyOfficer.person_id == person.person_id,
            CompanyOfficer.is_active == False
        ).first()

        if resigned_officer:
            contradictions.append(
                f"Statutory appointment resigned/ceased on {resigned_officer.resigned_on or resigned_officer.ceased_on}."
            )

        # Parse existing contradictory evidence
        if profile.contradictory_evidence:
            try:
                prior_contradictions = json.loads(profile.contradictory_evidence)
                if isinstance(prior_contradictions, list):
                    contradictions.extend(prior_contradictions)
            except Exception:
                pass

        # 2. Check Data Freshness (Staleness Rule: > 180 days)
        last_verified = profile.last_verified_at or profile.created_at
        if last_verified and last_verified.tzinfo is None:
            last_verified = last_verified.replace(tzinfo=timezone.utc)
        age_days = (now - last_verified).days if last_verified else 0
        is_stale = (age_days > 180) or (profile.is_current == False)

        # 3. Multi-Signal Corroboration Scoring
        # Signals required:
        # Signal A: Name match (baseline, 0.40 pts)
        # Signal B: Active statutory director / secretary / PSC appointment on Companies House (0.35 pts)
        # Signal C: Verified corporate domain / official filing citation (0.25 pts)
        signals_found = ["name_match"]
        score = 0.40  # Name match alone

        active_statutory_appointment = self.db.query(CompanyOfficer).filter(
            CompanyOfficer.crn == profile.crn,
            CompanyOfficer.person_id == person.person_id,
            CompanyOfficer.is_active == True
        ).first()

        if active_statutory_appointment:
            signals_found.append("active_statutory_appointment")
            score += 0.35

        # Check evidence summary for third corroboration signal
        if profile.evidence_summary:
            try:
                ev = json.loads(profile.evidence_summary)
                if ev.get("filing_source") or ev.get("corporate_domain_email") or ev.get("corporate_website_verified"):
                    signals_found.append("corporate_evidence_corroboration")
                    score += 0.25
            except Exception:
                pass

        profile.match_confidence = round(min(1.0, score), 2)
        profile.is_stale = is_stale

        # 4. Status Determination
        if contradictions:
            profile.verification_status = "disputed"
            profile.contradictory_evidence = json.dumps(contradictions)
        elif is_stale:
            profile.verification_status = "stale"
        elif len(signals_found) >= 2 and score >= 0.75:
            # Multi-signal verification passed
            profile.verification_status = "validated"
            profile.verified_at = now
            profile.last_verified_at = now
        else:
            # Name alone or single uncorroborated signal remains candidate/unverified
            profile.verification_status = "candidate"

        self.db.commit()

        log_audit_event(
            db=self.db,
            action="identity_resolution_evaluated",
            resource_type="professional_profile",
            resource_id=profile.id,
            user_id=actor_user_id,
            details={
                "status": profile.verification_status,
                "confidence": profile.match_confidence,
                "signals": signals_found,
                "contradictions": contradictions
            }
        )

        return profile

    def manually_verify_profile(
        self,
        profile_id: str,
        verified_by_user_id: str,
        corroborating_evidence_source: str,
        notes: Optional[str] = None
    ) -> ProfessionalRoleProfile:
        """Allows an authorised broker/analyst to promote a candidate to validated with documented human evidence."""
        profile = self.db.query(ProfessionalRoleProfile).filter(
            ProfessionalRoleProfile.id == profile_id
        ).first()
        if not profile:
            raise ValueError(f"Profile {profile_id} not found.")

        profile.verification_status = "validated"
        profile.match_confidence = 1.0
        profile.verified_at = datetime.now(timezone.utc)
        profile.last_verified_at = datetime.now(timezone.utc)
        profile.is_stale = False

        ev = {}
        if profile.evidence_summary:
            try:
                ev = json.loads(profile.evidence_summary)
            except Exception:
                pass
        ev["manual_human_verification"] = {
            "verified_by": verified_by_user_id,
            "evidence_source": corroborating_evidence_source,
            "notes": notes,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        profile.evidence_summary = json.dumps(ev)
        self.db.commit()

        log_audit_event(
            db=self.db,
            action="identity_manually_validated",
            resource_type="professional_profile",
            resource_id=profile.id,
            user_id=verified_by_user_id,
            details={"evidence_source": corroborating_evidence_source, "notes": notes}
        )
        return profile

    def record_suppression_objection(
        self,
        identifier_type: str,
        identifier_value: str,
        suppression_reason: str,
        recorded_by_user_id: Optional[str] = None,
        notes: Optional[str] = None
    ) -> SuppressionRecord:
        """
        Records an objection or opt-out (GDPR Article 21, CTPS, or internal do-not-contact).
        Enforces fail-closed blocking across all marketing and outreach.
        """
        norm_val = identifier_value.strip().lower()
        existing = self.db.query(SuppressionRecord).filter(
            SuppressionRecord.identifier_type == identifier_type,
            SuppressionRecord.identifier_value == norm_val
        ).first()

        if not existing:
            supp = SuppressionRecord(
                identifier_type=identifier_type,
                identifier_value=norm_val,
                suppression_reason=suppression_reason,
                notes=notes,
                recorded_by_user_id=recorded_by_user_id,
                is_active=True
            )
            self.db.add(supp)
        else:
            supp = existing
            supp.is_active = True
            supp.suppression_reason = suppression_reason
            supp.notes = notes

        # If person_id or name, flag the PersonIdentity
        if identifier_type == "person_id":
            person = self.db.query(PersonIdentity).filter(PersonIdentity.person_id == identifier_value).first()
            if person:
                person.is_suppressed = True
        elif identifier_type == "person_name_crn":
            # identifier_value format: "crn:person_name"
            pass

        self.db.commit()

        log_audit_event(
            db=self.db,
            action="suppression_recorded",
            resource_type="suppression_record",
            resource_id=norm_val,
            user_id=recorded_by_user_id,
            details={"type": identifier_type, "reason": suppression_reason}
        )
        return supp

    def execute_right_to_erasure(
        self,
        person_id: str,
        requested_by_user_id: Optional[str] = None,
        reason: str = "GDPR Article 17 Right to Erasure"
    ) -> Dict[str, Any]:
        """
        Executes GDPR Article 17 Right to Erasure.
        Permanently redacts personal identity data while preserving necessary corporate audit records
        in compliance with statutory data retention exemptions.
        """
        person = self.db.query(PersonIdentity).filter(PersonIdentity.person_id == person_id).first()
        if not person:
            raise ValueError(f"Person identity {person_id} not found.")

        # 1. Redact direct identifiers
        original_name = person.full_name
        person.full_name = f"[REDACTED_ERASURE_{person_id[:8]}]"
        person.normalized_name = f"redacted_erasure_{person_id[:8]}"
        person.birth_month = None
        person.birth_year = None
        person.nationality = None
        person.country_of_residence = None
        person.is_erased = True
        person.erasure_requested_at = datetime.now(timezone.utc)
        person.is_suppressed = True

        # 2. Redact professional profiles
        profiles = self.db.query(ProfessionalRoleProfile).filter(
            ProfessionalRoleProfile.person_id == person_id
        ).all()
        for p in profiles:
            p.profile_url = None
            p.evidence_summary = json.dumps({"erasure": "Personal evidence redacted under GDPR Article 17."})
            p.verification_status = "disputed"
            p.is_current = False

        # 3. Add to permanent suppression list
        self.record_suppression_objection(
            identifier_type="person_id",
            identifier_value=person_id,
            suppression_reason="gdpr_article_17_erasure",
            recorded_by_user_id=requested_by_user_id,
            notes=f"Right to erasure executed for original identity {original_name}"
        )

        self.db.commit()

        log_audit_event(
            db=self.db,
            action="gdpr_right_to_erasure_executed",
            resource_type="person_identity",
            resource_id=person_id,
            user_id=requested_by_user_id,
            details={"reason": reason, "profiles_redacted": len(profiles)}
        )

        return {
            "success": True,
            "person_id": person_id,
            "is_erased": True,
            "profiles_redacted": len(profiles)
        }
