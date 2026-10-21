from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.models.company import CompanyRegistry
from backend.app.models.score import LiquidityScoreRecord
from backend.app.models.investigation import CommercialOpportunityAssessment
from backend.app.models.decision_maker import (
    PersonIdentity,
    CompanyOfficer,
    ProfessionalRoleProfile,
    SuppressionRecord
)

class GateDimensionResult(BaseModel):
    dimension: str
    passed: bool
    score: float
    threshold: float
    rationale: str

class ValidationGateResult(BaseModel):
    crn: str
    profile_id: Optional[str]
    person_id: Optional[str]
    person_name: str
    role_title: str
    overall_passed: bool
    dimensions: List[GateDimensionResult]
    failing_reasons: List[str]
    is_marketing_eligible: bool
    can_activate_audience: bool
    evaluated_at: datetime

class DecisionMakerValidationGate:
    """
    Enforces strict, independent validation gates across company opportunity,
    person identity, decision-maker relevance, data freshness, and marketing eligibility.
    
    Guarantees that a high company strain score NEVER compensates for an uncertain identity
    or missing marketing permission.
    """

    def __init__(self, db: Session):
        self.db = db

    def evaluate_decision_maker_gates(
        self,
        crn: str,
        profile_id: str,
        intended_channel: str = "direct_broker_outreach"
    ) -> ValidationGateResult:
        """
        Evaluates the 5 independent validation dimensions.
        Fails closed if any single dimension fails.
        """
        profile = self.db.query(ProfessionalRoleProfile).filter(
            ProfessionalRoleProfile.id == profile_id,
            ProfessionalRoleProfile.crn == crn
        ).first()

        if not profile:
            raise ValueError(f"Professional profile {profile_id} on CRN {crn} not found.")

        person = profile.person
        company = self.db.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first()

        dimensions: List[GateDimensionResult] = []
        failing_reasons: List[str] = []

        # =====================================================================
        # GATE 1: Company Opportunity Confidence
        # Threshold: P(Opp) >= 0.50 OR Liquidity Strain Score >= 50.0
        # =====================================================================
        assessment = self.db.query(CommercialOpportunityAssessment).filter(
            CommercialOpportunityAssessment.crn == crn
        ).order_by(CommercialOpportunityAssessment.created_at.desc()).first()

        score_rec = self.db.query(LiquidityScoreRecord).filter(
            LiquidityScoreRecord.crn == crn
        ).order_by(LiquidityScoreRecord.scored_at.desc()).first()

        p_opp = assessment.p_qualified_opportunity if assessment else None
        strain = score_rec.score if score_rec else None

        opp_score = p_opp if p_opp is not None else ((strain / 100.0) if strain is not None else 0.0)
        gate_1_passed = opp_score >= 0.50

        if gate_1_passed:
            gate_1_rationale = f"Company qualified for finance opportunity (P(Opp)={opp_score:.2f} >= 0.50 threshold)."
        else:
            gate_1_rationale = f"Company opportunity confidence insufficient (P(Opp)={opp_score:.2f} < 0.50 threshold). Unqualified company."
            failing_reasons.append(gate_1_rationale)

        dimensions.append(GateDimensionResult(
            dimension="company_opportunity_confidence",
            passed=gate_1_passed,
            score=round(opp_score, 2),
            threshold=0.50,
            rationale=gate_1_rationale
        ))

        # =====================================================================
        # GATE 2: Person Identity Confidence
        # Threshold: match_confidence >= 0.75, verification_status == "validated",
        # no disputed contradictions. Names alone FAIL this gate.
        # =====================================================================
        id_conf = profile.match_confidence or 0.0
        is_validated = (profile.verification_status == "validated")
        has_contradictions = (profile.verification_status == "disputed") or bool(profile.contradictory_evidence)

        gate_2_passed = (id_conf >= 0.75) and is_validated and not has_contradictions

        if gate_2_passed:
            gate_2_rationale = f"Identity multi-signal validated (confidence {id_conf:.2f} >= 0.75; corroboration confirmed)."
        else:
            if has_contradictions:
                gate_2_rationale = f"Identity disputed due to contradictory evidence: {profile.contradictory_evidence}."
            elif not is_validated:
                gate_2_rationale = f"Identity is '{profile.verification_status}' (not validated). Names alone are insufficient."
            else:
                gate_2_rationale = f"Identity confidence ({id_conf:.2f}) below required 0.75 threshold."
            failing_reasons.append(gate_2_rationale)

        dimensions.append(GateDimensionResult(
            dimension="person_identity_confidence",
            passed=gate_2_passed,
            score=round(id_conf, 2),
            threshold=0.75,
            rationale=gate_2_rationale
        ))

        # =====================================================================
        # GATE 3: Decision-Maker Relevance
        # Must be genuine finance decision maker (CFO, FD, MD, CEO, Owner, Partner)
        # PSC alone without operational role FAILS this gate.
        # =====================================================================
        is_dm = profile.is_finance_decision_maker
        role_score = 1.0 if is_dm else 0.0
        gate_3_passed = is_dm

        if gate_3_passed:
            gate_3_rationale = f"Role '{profile.role_title}' verified as primary finance decision-maker."
        else:
            gate_3_rationale = f"Role '{profile.role_title}' is not a designated finance decision-maker (PSC or secondary roles not assumed to be finance buyer)."
            failing_reasons.append(gate_3_rationale)

        dimensions.append(GateDimensionResult(
            dimension="decision_maker_relevance",
            passed=gate_3_passed,
            score=role_score,
            threshold=1.0,
            rationale=gate_3_rationale
        ))

        # =====================================================================
        # GATE 4: Data Freshness
        # Threshold: not stale (< 180 days unverified), appointment active
        # =====================================================================
        is_fresh = (not profile.is_stale) and profile.is_current
        gate_4_passed = is_fresh

        if gate_4_passed:
            gate_4_rationale = "Data freshness verified (< 180 days; active current tenure)."
        else:
            gate_4_rationale = "Profile is stale or appointment has ceased (> 180 days unverified or inactive tenure)."
            failing_reasons.append(gate_4_rationale)

        dimensions.append(GateDimensionResult(
            dimension="data_freshness",
            passed=gate_4_passed,
            score=1.0 if is_fresh else 0.0,
            threshold=1.0,
            rationale=gate_4_rationale
        ))

        # =====================================================================
        # GATE 5: Marketing Eligibility & Suppression Checks
        # Must NOT be erased or suppressed (GDPR Art 17 / 21, CTPS).
        # =====================================================================
        is_erased = person.is_erased
        is_suppressed = person.is_suppressed

        # Check suppression table
        supp_hit = self.db.query(SuppressionRecord).filter(
            SuppressionRecord.is_active == True,
            SuppressionRecord.identifier_value.in_([
                person.person_id.lower(),
                person.normalized_name.lower(),
                crn.lower()
            ])
        ).first()

        gate_5_passed = not (is_erased or is_suppressed or supp_hit)

        if gate_5_passed:
            gate_5_rationale = "Marketing eligibility cleared. No active suppression or objection records found."
        else:
            reasons = []
            if is_erased:
                reasons.append("Person exercised GDPR Right to Erasure.")
            if is_suppressed or supp_hit:
                reason = supp_hit.suppression_reason if supp_hit else "Objection/Opt-out"
                reasons.append(f"Subject to active suppression: {reason}.")
            gate_5_rationale = " ".join(reasons)
            failing_reasons.append(gate_5_rationale)

        dimensions.append(GateDimensionResult(
            dimension="marketing_eligibility",
            passed=gate_5_passed,
            score=1.0 if gate_5_passed else 0.0,
            threshold=1.0,
            rationale=gate_5_rationale
        ))

        # =====================================================================
        # OVERALL GATE RESULT: Strict Fail-Closed Conjunction
        # High company strain score CANNOT compensate for failing person gates.
        # =====================================================================
        overall_passed = all(d.passed for d in dimensions)

        return ValidationGateResult(
            crn=crn,
            profile_id=profile.id,
            person_id=person.person_id,
            person_name=person.full_name,
            role_title=profile.role_title,
            overall_passed=overall_passed,
            dimensions=dimensions,
            failing_reasons=failing_reasons,
            is_marketing_eligible=gate_5_passed,
            can_activate_audience=overall_passed,
            evaluated_at=datetime.now(timezone.utc)
        )
