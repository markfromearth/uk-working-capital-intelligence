import re
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.decision_maker import (
    AudienceActivation,
    CampaignPerformanceMetric,
    SuppressionRecord,
    PersonIdentity,
    ProfessionalRoleProfile
)
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.services.decision_maker_validation import DecisionMakerValidationGate
from backend.app.services.audit import log_audit_event

PROHIBITED_DISTRESS_TERMS = re.compile(
    r"\b(distress|insolvent|insolvency|liquidation|wound\s+up|default|delinquent|desperate|"
    r"struggling|bankruptcy|winding\s+up|court\s+judgment|ccj\s+bailiff)\b",
    re.IGNORECASE
)

class AudienceActivationService:
    """
    Manages compliant audience activations, enforces fail-closed privacy gates,
    prohibits distress-based named targeting, and tracks commercial outcomes without
    confusing advert impressions with intent or causation.
    """

    def __init__(self, db: Session):
        self.db = db

    def create_activation_request(
        self,
        campaign_name: str,
        crn: str,
        destination_platform: str,
        activation_type: str,
        purpose_specification: str,
        lawful_basis: str,
        legitimate_interest_assessment_ref: Optional[str] = None,
        professional_profile_id: Optional[str] = None,
        retention_days: int = 90,
        requester_user_id: Optional[str] = None
    ) -> AudienceActivation:
        """
        Creates an audience activation proposal.
        Fails closed: proposals remain 'pending_compliance_review' until explicit compliance sign-off.
        """
        # 1. Prohibited Distress Targeting Protection
        # Platform rule: Never target named individuals or prospects asserting inferred distress
        if PROHIBITED_DISTRESS_TERMS.search(campaign_name) or PROHIBITED_DISTRESS_TERMS.search(purpose_specification):
            raise ValueError(
                "Activation rejected: Purpose or campaign name contains prohibited financial distress or vulnerability assertions. "
                "The platform communicates business services without asserting personal or corporate distress."
            )

        # 2. Scope & Personal Data Validation
        targeting_scope = "company_level"
        person_id = None
        if professional_profile_id:
            targeting_scope = "named_individual"
            profile = self.db.query(ProfessionalRoleProfile).filter(
                ProfessionalRoleProfile.id == professional_profile_id,
                ProfessionalRoleProfile.crn == crn
            ).first()
            if not profile:
                raise ValueError(f"Professional profile {professional_profile_id} on CRN {crn} does not exist.")
            
            # Check if attempting to upload raw statutory PSC data to public ad networks
            if profile.platform == "companies_house" and destination_platform in ("facebook_ads", "google_ads", "tiktok_ads"):
                raise ValueError(
                    f"Compliance violation: Direct statutory Companies House personal records cannot be uploaded to consumer ad platforms ({destination_platform}). "
                    "Approved channels are business-to-business platform criteria, company-level targeting, or broker-led outreach."
                )

            # Evaluate Validation Gate
            gate = DecisionMakerValidationGate(self.db)
            gate_res = gate.evaluate_decision_maker_gates(crn, professional_profile_id)
            if not gate_res.overall_passed:
                failing_str = "; ".join(gate_res.failing_reasons)
                raise ValueError(f"Cannot propose activation for decision-maker failing validation gates: {failing_str}")

            person_id = profile.person_id

        # 3. Create Pending Activation Record
        activation = AudienceActivation(
            campaign_name=campaign_name,
            crn=crn,
            person_id=person_id,
            professional_profile_id=professional_profile_id,
            activation_type=activation_type,
            destination_platform=destination_platform,
            targeting_scope=targeting_scope,
            purpose_specification=purpose_specification,
            lawful_basis=lawful_basis,
            legitimate_interest_assessment_ref=legitimate_interest_assessment_ref,
            transparency_notice_provided=True,
            retention_schedule_days=retention_days,
            compliance_approval_status="pending_compliance_review",
            execution_status="pending",
            suppression_cleared=False,
            asserts_financial_distress=False
        )

        self.db.add(activation)
        self.db.commit()

        log_audit_event(
            db=self.db,
            action="audience_activation_requested",
            resource_type="audience_activation",
            resource_id=activation.activation_id,
            user_id=requester_user_id,
            details={
                "campaign_name": campaign_name,
                "scope": targeting_scope,
                "destination": destination_platform,
                "lawful_basis": lawful_basis
            }
        )

        return activation

    def review_and_approve_activation(
        self,
        activation_id: str,
        reviewer_user_id: str,
        approve: bool,
        review_notes: str
    ) -> AudienceActivation:
        """
        Compliance review sign-off.
        Performs suppression check and sets approval status.
        """
        activation = self.db.query(AudienceActivation).filter(
            AudienceActivation.activation_id == activation_id
        ).first()

        if not activation:
            raise ValueError(f"Activation {activation_id} not found.")

        if not approve:
            activation.compliance_approval_status = "rejected"
            activation.execution_status = "failed"
            activation.compliance_reviewer_id = reviewer_user_id
            activation.compliance_reviewed_at = datetime.now(timezone.utc)
            activation.compliance_review_notes = review_notes
            self.db.commit()
            return activation

        # Check suppression registry
        is_suppressed = False
        supp_identifiers = [activation.crn.lower()]
        if activation.person_id:
            person = self.db.query(PersonIdentity).filter(PersonIdentity.person_id == activation.person_id).first()
            if person:
                supp_identifiers.extend([person.person_id.lower(), person.normalized_name.lower()])
                if person.is_erased or person.is_suppressed:
                    is_suppressed = True

        supp_hit = self.db.query(SuppressionRecord).filter(
            SuppressionRecord.is_active == True,
            SuppressionRecord.identifier_value.in_(supp_identifiers)
        ).first()

        if is_suppressed or supp_hit:
            activation.compliance_approval_status = "rejected"
            activation.execution_status = "suppressed"
            activation.suppression_cleared = False
            activation.compliance_review_notes = f"Rejected: Subject to suppression ({supp_hit.suppression_reason if supp_hit else 'Opt-out'})."
            self.db.commit()
            return activation

        # Approved
        activation.compliance_approval_status = "approved"
        activation.compliance_reviewer_id = reviewer_user_id
        activation.compliance_reviewed_at = datetime.now(timezone.utc)
        activation.compliance_review_notes = review_notes
        activation.suppression_checked_at = datetime.now(timezone.utc)
        activation.suppression_cleared = True

        self.db.commit()

        log_audit_event(
            db=self.db,
            action="audience_activation_compliance_approved",
            resource_type="audience_activation",
            resource_id=activation.activation_id,
            user_id=reviewer_user_id,
            details={"approved": True, "notes": review_notes}
        )

        return activation

    def execute_activation(
        self,
        activation_id: str,
        actor_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes approved activation against target destination.
        Enforces strict fail-closed constraints.
        """
        activation = self.db.query(AudienceActivation).filter(
            AudienceActivation.activation_id == activation_id
        ).first()

        if not activation:
            raise ValueError(f"Activation {activation_id} not found.")

        # Fail-closed compliance checks
        if activation.compliance_approval_status != "approved":
            raise ValueError(f"Fail-closed: Activation {activation_id} is not compliance-approved (Status: {activation.compliance_approval_status}).")

        if activation.asserts_financial_distress:
            raise ValueError("Fail-closed: Prohibited targeting asserting financial distress.")

        # Re-check suppression at execution time
        if activation.person_id:
            person = self.db.query(PersonIdentity).filter(PersonIdentity.person_id == activation.person_id).first()
            if person and (person.is_erased or person.is_suppressed):
                activation.execution_status = "suppressed"
                self.db.commit()
                raise ValueError("Fail-closed: Target person exercised Right to Erasure / Objection prior to execution.")

        activation.execution_status = "active"
        activation.executed_at = datetime.now(timezone.utc)
        self.db.commit()

        log_audit_event(
            db=self.db,
            action="audience_activation_executed",
            resource_type="audience_activation",
            resource_id=activation_id,
            user_id=actor_user_id,
            details={"destination": activation.destination_platform, "scope": activation.targeting_scope}
        )

        return {
            "success": True,
            "activation_id": activation_id,
            "status": "active",
            "destination": activation.destination_platform,
            "targeting_scope": activation.targeting_scope
        }

    def record_campaign_performance(
        self,
        activation_id: str,
        spend_gbp: float,
        impressions_count: int,
        clicks_count: int,
        engagements_count: int,
        inquiries_received: int = 0,
        qualified_conversations: int = 0,
        facilities_funded_count: int = 0,
        funded_volume_gbp: float = 0.0
    ) -> CampaignPerformanceMetric:
        """
        Records campaign performance metrics.
        Separates advert impressions from commercial intent or verified qualification.
        """
        activation = self.db.query(AudienceActivation).filter(
            AudienceActivation.activation_id == activation_id
        ).first()
        if not activation:
            raise ValueError(f"Activation {activation_id} not found.")

        metric = CampaignPerformanceMetric(
            activation_id=activation_id,
            observation_date=datetime.now(timezone.utc),
            spend_gbp=spend_gbp,
            impressions_count=impressions_count,
            clicks_count=clicks_count,
            engagements_count=engagements_count,
            inquiries_received=inquiries_received,
            qualified_conversations=qualified_conversations,
            facilities_funded_count=facilities_funded_count,
            funded_volume_gbp=funded_volume_gbp
        )
        self.db.add(metric)

        # If a facility was funded through this campaign, record a commercial funnel outcome
        if facilities_funded_count > 0 and funded_volume_gbp > 0:
            outcome = CommercialFunnelOutcome(
                crn=activation.crn,
                outcome_stage="FACILITY_FUNDED",
                facility_type="invoice_discounting",
                funded_amount_gbp=funded_volume_gbp,
                realized_revenue_gbp=round(funded_volume_gbp * 0.02, 2),  # 2.0% broker commission
                total_investigation_cost_gbp=spend_gbp,
                feedback_notes=f"Converted via compliant audience campaign '{activation.campaign_name}' ({activation.destination_platform})"
            )
            self.db.add(outcome)

        self.db.commit()
        return metric
