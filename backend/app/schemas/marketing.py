"""
Pydantic Schemas for Campaign Strategy and Audience Activation.
Defines structured contracts for evidence-backed B2B marketing concepts and validation plans.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class ContentFormat(str, Enum):
    PODCAST_SPONSORSHIP = "PODCAST_SPONSORSHIP"
    EXPERT_ROUNDTABLE = "EXPERT_ROUNDTABLE"
    TRADE_PUBLICATION_ARTICLE = "TRADE_PUBLICATION_ARTICLE"
    SECTOR_BENCHMARK_GUIDE = "SECTOR_BENCHMARK_GUIDE"
    ASSOCIATION_WEBINAR = "ASSOCIATION_WEBINAR"
    CASH_FLOW_CALCULATOR = "CASH_FLOW_CALCULATOR"
    DIRECT_EXECUTIVE_BRIEF = "DIRECT_EXECUTIVE_BRIEF"


class ValidationTestPlan(BaseModel):
    """Low-cost, bounded validation experiment before material spend."""
    test_name: str
    max_test_budget_gbp: float = 0.00  # Default £0.00 for free validation
    duration_weeks: int = 4
    success_criteria: str
    failure_stop_criteria: str


class CampaignStrategy(BaseModel):
    """Structured, evidence-grounded B2B commercial finance marketing strategy."""
    strategy_id: str = Field(default_factory=lambda: f"STRAT-{str(uuid.uuid4())[:8].upper()}")
    segment_name: str
    finance_product: str
    target_role: str
    commercial_hypothesis: str
    supporting_evidence: List[str] = Field(default_factory=list)
    recommended_channel_type: str
    recommended_channel_name: str
    content_format: ContentFormat
    content_theme: str
    campaign_headline: str
    executive_message: str
    compliance_guardrails: List[str] = Field(default_factory=list)
    validation_test: ValidationTestPlan
    estimated_lead_time_days: int = 14
    confidence_score: float = 0.85
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScenarioType(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    BASE = "BASE"
    OPTIMISTIC = "OPTIMISTIC"


class ScenarioEconomics(BaseModel):
    """Detailed unit economics for a single scenario."""
    scenario_type: ScenarioType
    assumed_facility_size_gbp: float
    engagement_rate: float
    qualified_lead_rate: float
    application_rate: float
    funding_approval_rate: float
    broker_commission_rate: float
    
    # Modeled cohort metrics (assuming test cohort or target segment)
    target_reach: int
    estimated_engagements: float
    estimated_qualified_leads: float
    estimated_applications: float
    estimated_funded_deals: float
    
    gross_commission_gbp: float
    campaign_cost_gbp: float
    net_value_gbp: float
    roi_multiple: float
    cost_per_qualified_lead_gbp: float
    customer_acquisition_cost_gbp: float


class CampaignEconomicsModel(BaseModel):
    """Full-funnel commercial unit economics model with uncertainty bounds."""
    model_id: str = Field(default_factory=lambda: f"ECON-{str(uuid.uuid4())[:8].upper()}")
    strategy_id: Optional[str] = None
    indicative_facility_size_gbp: float
    campaign_cost_gbp: float
    target_reach: int
    scenarios: Dict[str, ScenarioEconomics]
    
    # Decision boundaries
    break_even_qualified_leads: float
    break_even_response_rate: float
    max_recommended_test_budget_gbp: float
    expected_net_value_gbp: float  # Probability-weighted across scenarios
    
    # Grounding & Assumption isolation
    is_historical_benchmark: bool = False
    assumptions_log: List[str] = Field(default_factory=list)
    risk_assessment: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
