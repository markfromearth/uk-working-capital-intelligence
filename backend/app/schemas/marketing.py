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
