import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class InvestigationDecision(Base):
    """
    Records Value-of-Information (EVOI) evaluation, budgeting checks,
    and investigation decisions (INVESTIGATE, DEFER, STOP, ESCALATE)
    with full cost accounting and auditable rationale.
    """
    __tablename__ = "investigation_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(String(36), default=generate_uuid, unique=True, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = Column(String(100), nullable=False, index=True)
    
    # Value-of-Information Economics
    evoi_value = Column(Float, nullable=False)               # Expected Value of Information (£)
    estimated_cost_gbp = Column(Float, nullable=False)       # Anticipated query/API fee (£)
    actual_cost_gbp = Column(Float, default=0.0)             # Incurred cost (£)
    uncertainty_prior = Column(Float, nullable=False)        # Decision uncertainty before query [0, 1]
    uncertainty_posterior_expected = Column(Float, nullable=False) # Anticipated uncertainty after query
    
    # Decision Policy Outcome
    decision = Column(String(30), nullable=False)            # INVESTIGATE, DEFER, STOP, ESCALATE_HUMAN_REVIEW
    decision_rationale = Column(Text, nullable=False)
    budget_remaining_gbp = Column(Float, nullable=True)      # Daily company budget remaining
    
    # Execution Tracking
    status = Column(String(30), default="pending", index=True) # pending, executed, skipped, failed
    result_summary = Column(Text, nullable=True)
    error_details = Column(Text, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)

class CommercialOpportunityAssessment(Base):
    """
    Multidimensional assessment evaluating probability of finance demand,
    conversion to funded facility, finance product relevance, and estimated commercial revenue.
    """
    __tablename__ = "commercial_opportunity_assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(String(36), default=generate_uuid, unique=True, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    model_version = Column(String(50), nullable=False, default="v1.0.0-rules")
    assessment_type = Column(String(30), default="rules_baseline") # rules_baseline, ml_calibrated, hybrid_shadow
    
    # Calibrated Probabilities & Economics
    p_qualified_opportunity = Column(Float, nullable=False)     # P(Genuine funding demand) [0.0 - 1.0]
    p_conversion_funded = Column(Float, nullable=False)         # P(Funded facility outcome) [0.0 - 1.0]
    estimated_facility_size_gbp = Column(Float, default=0.0)    # Estimated facility needed (£)
    estimated_commercial_contribution_gbp = Column(Float, default=0.0) # Expected broker fee (£)
    
    # Product Fit Matrix (0.0 to 100.0)
    invoice_finance_fit = Column(Float, default=0.0)            # Invoice Factoring / Discounting
    asset_refinance_fit = Column(Float, default=0.0)            # Plant, Machinery, Property Refinance
    term_loan_fit = Column(Float, default=0.0)                  # Commercial Working Capital Loan
    
    # Evidence & Action
    evidence_uncertainty = Column(Float, default=0.5)           # Model uncertainty / missing data index
    recommended_action = Column(String(100), nullable=False)    # Immediate outreach, watchlist, discard, etc.
    explanation_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)

class CommercialFunnelOutcome(Base):
    """
    Captures verified commercial outcomes across the broker sales funnel
    to train and calibrate the machine learning learning loop.
    """
    __tablename__ = "commercial_funnel_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    outcome_id = Column(String(36), default=generate_uuid, unique=True, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    broker_user_id = Column(String(36), nullable=True)
    
    # Funnel Stage
    outcome_stage = Column(String(50), nullable=False, index=True) 
    # NO_RESPONSE, QUALIFIED_CONVERSATION, BROKER_ACCEPTED, APPLICATION_SUBMITTED,
    # LENDER_DECLINED, FACILITY_OFFERED, FACILITY_FUNDED
    
    facility_type = Column(String(50), nullable=True)            # invoice_finance, asset_refinance, term_loan
    funded_amount_gbp = Column(Float, default=0.0)              # Actual facility drawn (£)
    realized_revenue_gbp = Column(Float, default=0.0)           # Actual broker fee earned (£)
    loss_reason = Column(String(100), nullable=True)            # e.g. 'rate_too_high', 'credit_declined', 'unresponsive'
    
    # Operational Cost & Latency
    time_to_outcome_days = Column(Float, nullable=True)
    total_investigation_cost_gbp = Column(Float, default=0.0)
    feedback_notes = Column(Text, nullable=True)
    recorded_at = Column(DateTime, default=utcnow, index=True)

class InvestigationRunLog(Base):
    """
    Tracks empirical outcomes of executed investigations to learn actual EVOI information gain.
    Records prior/posterior tiers, actions, and uncertainty, and whether new evidence shifted broker decision.
    """
    __tablename__ = "investigation_run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(Integer, ForeignKey("investigation_decisions.id", ondelete="SET NULL"), nullable=True, index=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = Column(String(100), nullable=False, index=True)
    missingness_pattern = Column(String(100), nullable=True, index=True)
    
    prior_action = Column(String(100), nullable=True)
    posterior_action = Column(String(100), nullable=True)
    prior_tier = Column(String(50), nullable=True)
    posterior_tier = Column(String(50), nullable=True)
    
    prior_uncertainty = Column(Float, nullable=False, default=0.5)
    posterior_uncertainty = Column(Float, nullable=False, default=0.5)
    action_changed = Column(Boolean, nullable=False, default=False)
    tool_success = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, index=True)
