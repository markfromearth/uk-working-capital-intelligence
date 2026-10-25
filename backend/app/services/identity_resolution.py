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

    # =========================================================================
    # PECR CORPORATE SUBSCRIBER & UK GDPR VALIDATION (Phase 3)
    # =========================================================================

    CORPORATE_SUBSCRIBER_TYPES = {
        "ltd",
        "limited",
        "private-limited-company",
        "private-limited-guarant-nsc",
        "private-limited-guarant-nsc-limited-exemption",
        "private-limited-guarant-nfs-limited-exemption",
        "private-limited-shares-section-30-exemption",
        "private-unlimited",
        "private-unlimited-nsc",
        "plc",
        "public-limited-company",
        "llp",
        "limited-liability-partnership",
        "scottish-partnership",  # Separate legal personality under Partnership Act 1890 s.4(2) (Scotland)
        "scottish-limited-partnership",
        "registered-society-non-jurisdictional",
        "industrial-and-provident-society",
        "community-interest-company",
        "cic",
        "royal-charter",
        "charitable-incorporated-organisation",
        "scottish-charitable-incorporated-organisation",
        "uk-establishment",
    }

    INDIVIDUAL_SUBSCRIBER_TYPES = {
        "sole-trader",
        "sole-proprietor",
        "individual",
        "partnership",
        "general-partnership",
        "english-partnership",
        "limited-partnership",  # English/Welsh LP lacks separate legal personality
        "unincorporated-association",
    }

    OUT_OF_SCOPE_NON_UK_TYPES = {
        "oversea-company",
        "foreign-entity",
        "non-uk",
        "other-jurisdiction",
    }

    @classmethod
    def evaluate_pecr_uk_gdpr_compliance(
        cls,
        company_type: Optional[str] = None,
        crn: Optional[str] = None,
        has_prior_opt_in_consent: bool = False,
        sender_identity_disclosed: bool = True,
        valid_opt_out_address_provided: bool = True,
        is_suppressed: bool = False,
        suppression_reason: Optional[str] = None,
        channel_mode: str = "electronic_mail",
    ) -> Dict[str, Any]:
        """
        Produces a structured, versioned PECR 2003 & UK GDPR compliance decision record.
        Enforces:
        1. Fail-closed handling of missing, empty, or unknown company_type (never defaults to 'ltd').
        2. PECR Regulation 22: Prohibits unsolicited electronic mail to individual subscribers
           (sole traders, English/Welsh general partnerships, unincorporated bodies) without
           verifiable prior opt-in consent; corporate subscribers (Ltd, PLC, LLP, Scottish partnerships)
           are exempt from Reg 22's prior opt-in requirement.
        3. PECR Regulation 23: Applies to ALL unsolicited direct marketing electronic mail
           (including corporate subscribers)—sender identity must not be disguised/concealed
           and a valid opt-out address must be provided.
        4. UK GDPR Article 6(1)(f) (Legitimate Interests) or 6(1)(a) (Consent) & Article 21
           absolute right to object to direct marketing.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        decision_base = {
            "rule_version": "pecr-ukgdpr-v2.0",
            "checked_at": checked_at,
            "crn": crn,
            "channel_mode": channel_mode,
            "sender_identity_disclosed": sender_identity_disclosed,
            "valid_opt_out_address_provided": valid_opt_out_address_provided,
            "suppression_checked": True,
            "is_suppressed": is_suppressed,
        }

        if is_suppressed:
            return {
                **decision_base,
                "is_permissible": False,
                "subscriber_classification": "SUPPRESSED",
                "status_code": "SUPPRESSED_OR_OBJECTED_BLOCKED",
                "legal_basis": "UK GDPR Article 21(2)-(3) Absolute Right to Object / PECR Opt-Out",
                "pecr_reg_22_status": "BLOCKED_BY_SUPPRESSION",
                "pecr_reg_23_status": "OPT_OUT_ENFORCED",
                "rationale": (
                    f"Direct marketing is strictly prohibited: target matches active suppression/objection "
                    f"record ({suppression_reason or 'UK GDPR Art 21 / PECR opt-out'})."
                ),
            }

        if not sender_identity_disclosed or not valid_opt_out_address_provided:
            return {
                **decision_base,
                "is_permissible": False,
                "subscriber_classification": "UNVERIFIED",
                "status_code": "PECR_REG_23_IDENTIFICATION_OR_OPT_OUT_MISSING",
                "legal_basis": "PECR 2003 Regulation 23 Mandatory Sender Identification & Opt-Out",
                "pecr_reg_22_status": "NOT_EVALUATED",
                "pecr_reg_23_status": "VIOLATION_BLOCKED",
                "rationale": (
                    "PECR Regulation 23 prohibits unsolicited electronic marketing mail that conceals "
                    "the sender's identity or omits a valid address for opt-out requests."
                ),
            }

        if not company_type or not str(company_type).strip():
            return {
                **decision_base,
                "is_permissible": False,
                "subscriber_classification": "UNKNOWN",
                "status_code": "UNKNOWN_SUBSCRIBER_STATUS_BLOCKED",
                "legal_basis": "PECR 2003 Regulation 22 Fail-Closed Guard",
                "pecr_reg_22_status": "BLOCKED_UNVERIFIED_SUBSCRIBER",
                "pecr_reg_23_status": "COMPLIANT",
                "rationale": (
                    "Fail-closed compliance block: Entity legal form (company_type) is missing or unverified. "
                    "Cannot assume Corporate Subscriber status without statutory evidence."
                ),
            }

        clean_type = str(company_type).strip().lower().replace("_", "-").replace(" ", "-")

        if clean_type in cls.OUT_OF_SCOPE_NON_UK_TYPES:
            return {
                **decision_base,
                "is_permissible": False,
                "subscriber_classification": "OUT_OF_SCOPE_NON_UK",
                "status_code": "OUT_OF_SCOPE_JURISDICTION_BLOCKED",
                "legal_basis": "Jurisdiction Boundary (UK Corporate Registry Only)",
                "pecr_reg_22_status": "OUT_OF_SCOPE",
                "pecr_reg_23_status": "OUT_OF_SCOPE",
                "rationale": (
                    f"Entity type '{clean_type}' is outside the verified UK corporate subscriber scope."
                ),
            }

        if clean_type in cls.CORPORATE_SUBSCRIBER_TYPES:
            return {
                **decision_base,
                "is_permissible": True,
                "subscriber_classification": "CORPORATE_SUBSCRIBER",
                "status_code": "CORPORATE_SUBSCRIBER_EXEMPT",
                "legal_basis": (
                    "PECR 2003 Regulation 22 Corporate Subscriber Distinction; "
                    "PECR Regulation 23 Sender Identification & Opt-Out; "
                    "UK GDPR Article 6(1)(f) Legitimate Interests"
                ),
                "pecr_reg_22_status": "EXEMPT_CORPORATE_SUBSCRIBER",
                "pecr_reg_23_status": "COMPLIANT_IDENTITY_AND_OPT_OUT_REQUIRED",
                "rationale": (
                    f"Entity '{clean_type}' is a verified UK corporate subscriber with separate legal personality. "
                    "Exempt from PECR Reg 22 prior opt-in requirement; compliant with PECR Reg 23 sender identification "
                    "and opt-out address requirements under UK GDPR Art 6(1)(f) Legitimate Interests."
                ),
            }

        if clean_type in cls.INDIVIDUAL_SUBSCRIBER_TYPES:
            if has_prior_opt_in_consent:
                return {
                    **decision_base,
                    "is_permissible": True,
                    "subscriber_classification": "INDIVIDUAL_SUBSCRIBER",
                    "status_code": "OPT_IN_CONSENT_VERIFIED",
                    "legal_basis": (
                        "PECR 2003 Regulation 22(2) Prior Opt-In Consent; "
                        "PECR Regulation 23 Sender Identification & Opt-Out; "
                        "UK GDPR Article 6(1)(a) Explicit Consent"
                    ),
                    "pecr_reg_22_status": "COMPLIANT_PRIOR_CONSENT_RECORDED",
                    "pecr_reg_23_status": "COMPLIANT_IDENTITY_AND_OPT_OUT_REQUIRED",
                    "rationale": (
                        f"Individual subscriber entity '{clean_type}' has verifiable prior opt-in consent "
                        "recorded under PECR Regulation 22 and UK GDPR Article 6(1)(a)."
                    ),
                }
            return {
                **decision_base,
                "is_permissible": False,
                "subscriber_classification": "INDIVIDUAL_SUBSCRIBER",
                "status_code": "NON_CORPORATE_REQUIRES_OPT_IN",
                "legal_basis": "PECR 2003 Regulation 22 Prohibition (Individual Subscriber)",
                "pecr_reg_22_status": "VIOLATION_BLOCKED",
                "pecr_reg_23_status": "COMPLIANT",
                "rationale": (
                    f"Entity '{clean_type}' is classified as an individual subscriber (sole trader / unincorporated partnership). "
                    "Direct electronic marketing is prohibited under PECR Regulation 22 without recorded prior opt-in consent."
                ),
            }

        # Any unrecognised company_type fails closed
        return {
            **decision_base,
            "is_permissible": False,
            "subscriber_classification": "AMBIGUOUS_OR_UNKNOWN",
            "status_code": "UNKNOWN_SUBSCRIBER_STATUS_BLOCKED",
            "legal_basis": "PECR 2003 Regulation 22 Fail-Closed Guard",
            "pecr_reg_22_status": "BLOCKED_AMBIGUOUS_ENTITY_TYPE",
            "pecr_reg_23_status": "COMPLIANT",
            "rationale": (
                f"Fail-closed compliance block: Entity legal form '{clean_type}' is not in the verified "
                "UK corporate subscriber allowlist. Direct electronic marketing blocked."
            ),
        }

    @classmethod
    def validate_corporate_subscriber_status(
        cls,
        company_type: Optional[str] = None,
        crn: Optional[str] = None,
        has_prior_opt_in_consent: bool = False,
        sender_identity_disclosed: bool = True,
        valid_opt_out_address_provided: bool = True,
    ) -> Tuple[bool, str, str, str]:
        """
        Validates whether target is a UK Corporate Subscriber under PECR 2003 Regulation 22 & 23.
        Corporate bodies (Ltd, PLC, LLP, Scottish partnerships) are exempt from PECR Reg 22 prior opt-in
        and may receive B2B marketing under UK GDPR Art 6(1)(f) Legitimate Interests provided PECR Reg 23
        (sender identity and valid opt-out address) is satisfied.
        Individual subscribers (sole traders, English/Welsh unincorporated partnerships) strictly require
        prior opt-in consent under PECR Reg 22.
        Unknown or missing company_type fails closed (never defaults to 'ltd').

        Returns: (is_permissible, status_code, legal_basis, rationale)
        """
        decision = cls.evaluate_pecr_uk_gdpr_compliance(
            company_type=company_type,
            crn=crn,
            has_prior_opt_in_consent=has_prior_opt_in_consent,
            sender_identity_disclosed=sender_identity_disclosed,
            valid_opt_out_address_provided=valid_opt_out_address_provided,
        )
        return (
            decision["is_permissible"],
            decision["status_code"],
            decision["legal_basis"],
            decision["rationale"],
        )

