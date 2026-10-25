import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class PersonIdentity(Base):
    """
    Canonical individual identity record (strictly separated from company identities).
    Represents an identifiable human being across corporate relationships.
    """
    __tablename__ = "person_identities"

    person_id = Column(String(36), primary_key=True, default=generate_uuid)
    full_name = Column(String(255), nullable=False, index=True)
    normalized_name = Column(String(255), nullable=False, index=True)
    title = Column(String(50), nullable=True)
    birth_month = Column(Integer, nullable=True)
    birth_year = Column(Integer, nullable=True)
    nationality = Column(String(100), nullable=True)
    country_of_residence = Column(String(100), nullable=True)

    # Privacy and erasure controls
    is_erased = Column(Boolean, default=False, index=True)  # GDPR Article 17 Right to Erasure
    erasure_requested_at = Column(DateTime, nullable=True)
    is_suppressed = Column(Boolean, default=False, index=True)  # GDPR Article 21 Objection / Opt-out

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    officer_roles = relationship("CompanyOfficer", back_populates="person", cascade="all, delete-orphan")
    professional_profiles = relationship("ProfessionalRoleProfile", back_populates="person", cascade="all, delete-orphan")

class CompanyOfficer(Base):
    """
    Official corporate officer and PSC records retrieved from statutory registries (e.g. Companies House).
    Maintains historical integrity including ceased and resigned appointments.
    """
    __tablename__ = "company_officers"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String(36), ForeignKey("person_identities.person_id", ondelete="SET NULL"), nullable=True, index=True)

    # Officer classification
    officer_name = Column(String(255), nullable=False, index=True)
    officer_role = Column(String(50), nullable=False, index=True)  # director, secretary, corporate-director, llp-member, psc_individual, psc_corporate
    relationship_category = Column(String(50), nullable=False, default="directorship")  # ownership, control, directorship, secretarial

    # Appointment timeline
    appointed_on = Column(DateTime, nullable=True)
    resigned_on = Column(DateTime, nullable=True)  # Populated when resigned/ceased
    notified_on = Column(DateTime, nullable=True)  # For PSC notifications
    ceased_on = Column(DateTime, nullable=True)    # For PSC cessation
    is_active = Column(Boolean, default=True, index=True)

    # PSC control specifications (JSON array)
    # e.g. ["ownership-of-shares-25-to-50-percent", "voting-rights-25-to-50-percent", "right-to-appoint-and-remove-directors"]
    natures_of_control = Column(Text, nullable=True)
    ownership_percentage_lower = Column(Float, nullable=True)  # e.g. 25.0
    ownership_percentage_upper = Column(Float, nullable=True)  # e.g. 50.0

    # Demographic attributes from official register (for identity resolution matching)
    occupation = Column(String(255), nullable=True)
    nationality = Column(String(100), nullable=True)
    date_of_birth_month = Column(Integer, nullable=True)
    date_of_birth_year = Column(Integer, nullable=True)

    # Provenance
    source_system = Column(String(50), nullable=False, default="companies_house")
    source_reference = Column(String(255), nullable=True)  # Appointment ID or PSC self-link
    recorded_at = Column(DateTime, default=utcnow)

    # Relationships
    person = relationship("PersonIdentity", back_populates="officer_roles")

class ProfessionalRoleProfile(Base):
    """
    Professional profile and role mapping linked to a Person and Canonical Company.
    Subject to strict independent identity resolution and data freshness validation.
    """
    __tablename__ = "professional_role_profiles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    person_id = Column(String(36), ForeignKey("person_identities.person_id", ondelete="CASCADE"), nullable=False, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)

    # Role details
    role_title = Column(String(255), nullable=False)  # e.g. Finance Director, CFO, Managing Director, Founder & CEO
    standardized_role = Column(String(50), default="other", nullable=True, index=True)  # cfo, finance_director, managing_director, ceo, owner, secretary, other
    seniority_level = Column(String(50), default="c_level", nullable=True)  # c_level, director, owner_partner, senior, operational

    # Decision-maker qualification
    is_finance_decision_maker = Column(Boolean, default=False, index=True)
    decision_authority_scope = Column(String(100), nullable=True)  # e.g. borrowing_and_banking, corporate_governance, general_management

    # Tenure
    is_current = Column(Boolean, default=True, index=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

    # Evidence & Verification
    platform = Column(String(50), default="companies_house", nullable=True)  # companies_house, linkedin_approved, corporate_site
    profile_url = Column(String(500), nullable=True)
    source_provider = Column(String(50), default="unverified_web_mention", nullable=True)
    evidence_summary = Column(Text, nullable=True)  # JSON with citations / filings
    contradictory_evidence = Column(Text, nullable=True)  # JSON documenting conflicting signals

    # Identity Resolution Confidence & Status
    # unverified -> candidate -> validated / disputed / stale
    match_confidence = Column(Float, default=0.50)  # Multi-signal matching score
    verification_status = Column(String(50), default="unverified", index=True)
    verified_at = Column(DateTime, nullable=True)
    last_verified_at = Column(DateTime, default=utcnow)
    is_stale = Column(Boolean, default=False, index=True)  # True if > 180 days without verification

    # Data minimization check
    contains_prohibited_personal_data = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    person = relationship("PersonIdentity", back_populates="professional_profiles")

class AudienceActivation(Base):
    """
    Documented marketing activation job with mandatory compliance review,
    lawful basis justification, suppression checks, and transparent destination boundaries.
    """
    __tablename__ = "audience_activations"

    activation_id = Column(String(36), primary_key=True, default=generate_uuid)
    campaign_name = Column(String(255), nullable=False, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String(36), ForeignKey("person_identities.person_id", ondelete="SET NULL"), nullable=True, index=True)
    professional_profile_id = Column(String(36), ForeignKey("professional_role_profiles.id", ondelete="SET NULL"), nullable=True)

    # Destination & Method
    # company_level_account, platform_native_criteria, broker_direct_outreach, first_party_crm
    activation_type = Column(String(50), nullable=False)
    destination_platform = Column(String(50), nullable=False)  # linkedin_company_targeting, platform_abm_native, direct_broker_outreach, hubspot_first_party
    targeting_scope = Column(String(50), default="company_level")  # company_level, role_criteria, named_individual

    # Compliance Gate Prerequisites (All required before activation can proceed)
    lawful_basis = Column(String(50), nullable=False)  # legitimate_interests, consent, contract
    legitimate_interest_assessment_ref = Column(String(255), nullable=True)
    transparency_notice_provided = Column(Boolean, default=False)
    purpose_specification = Column(String(255), nullable=False)  # B2B commercial finance facility awareness
    retention_schedule_days = Column(Integer, default=90)
    
    # Audit & Approval
    # pending_compliance_review, approved, rejected
    compliance_approval_status = Column(String(50), default="pending_compliance_review", index=True)
    compliance_reviewer_id = Column(String(36), nullable=True)
    compliance_reviewed_at = Column(DateTime, nullable=True)
    compliance_review_notes = Column(Text, nullable=True)

    # Suppression verification
    suppression_checked_at = Column(DateTime, nullable=True)
    suppression_cleared = Column(Boolean, default=False)

    # Execution State
    # pending, active, paused, suppressed, completed, failed
    execution_status = Column(String(50), default="pending", index=True)
    executed_at = Column(DateTime, nullable=True)
    execution_error = Column(Text, nullable=True)

    # Distress targeting protection (Must be False to execute)
    asserts_financial_distress = Column(Boolean, default=False)
    requester_user_id = Column(String(36), nullable=True, index=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    metrics = relationship("CampaignPerformanceMetric", back_populates="activation", cascade="all, delete-orphan")

class SuppressionRecord(Base):
    """
    Central suppression, opt-out, objection, and Right to Erasure registry.
    Enforces fail-closed blocking across all activation channels.
    """
    __tablename__ = "suppression_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # identifier_type: crn, person_id, full_name_crn, email_domain, phone
    identifier_type = Column(String(50), nullable=False, index=True)
    identifier_value = Column(String(255), nullable=False, index=True)
    
    # suppression_reason: gdpr_article_21_objection, gdpr_article_17_erasure, ctps_opt_out, broker_do_not_contact, competitor
    suppression_reason = Column(String(50), nullable=False, index=True)
    notes = Column(Text, nullable=True)
    recorded_by_user_id = Column(String(36), nullable=True)
    recorded_at = Column(DateTime, default=utcnow, index=True)
    is_active = Column(Boolean, default=True, index=True)

class CampaignPerformanceMetric(Base):
    """
    Campaign performance measurements.
    Strictly decouples advertising impressions from intent or commercial qualification.
    """
    __tablename__ = "campaign_performance_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    activation_id = Column(String(36), ForeignKey("audience_activations.activation_id", ondelete="CASCADE"), nullable=False, index=True)
    observation_date = Column(DateTime, nullable=False, index=True)

    # Platform metrics
    spend_gbp = Column(Float, default=0.0)
    impressions_count = Column(Integer, default=0)
    clicks_count = Column(Integer, default=0)
    engagements_count = Column(Integer, default=0)

    # Qualified business outcomes (from broker confirmation, NOT assumed from clicks)
    inquiries_received = Column(Integer, default=0)
    qualified_conversations = Column(Integer, default=0)
    facilities_funded_count = Column(Integer, default=0)
    funded_volume_gbp = Column(Float, default=0.0)

    recorded_at = Column(DateTime, default=utcnow)

    activation = relationship("AudienceActivation", back_populates="metrics")
