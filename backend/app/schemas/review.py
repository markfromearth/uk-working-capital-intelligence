from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class ReviewActionCreate(BaseModel):
    review_status: str = Field(..., pattern="^(unreviewed|in_review|verified_opportunity|suppressed|snoozed|disputed_signal)$")
    assigned_broker_name: Optional[str] = None
    signal_id_disputed: Optional[str] = None
    dispute_reason: Optional[str] = None
    notes: Optional[str] = None
    verification_request: Optional[str] = None
    snooze_days: Optional[int] = None
    outcome: Optional[str] = None  # funded_refinance, active_discussion, no_funding_need, unreachable, existing_facility
    outcome_notes: Optional[str] = None

class ReviewActionOut(BaseModel):
    id: str
    crn: str
    user_id: Optional[str] = None
    assigned_broker_name: Optional[str] = None
    review_status: str
    signal_id_disputed: Optional[str] = None
    dispute_reason: Optional[str] = None
    notes: Optional[str] = None
    verification_request: Optional[str] = None
    snooze_until: Optional[datetime] = None
    outcome: Optional[str] = None
    outcome_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class ProspectListItem(BaseModel):
    crn: str
    company_name: str
    company_status: str
    liquidity_strain_score: float
    score_band: str
    score_change_90d: float
    primary_signals_summary: str
    latest_signal_date: Optional[datetime] = None
    evidence_coverage: str
    review_status: str
    assigned_broker_name: Optional[str] = None
    last_updated: datetime
    is_synthetic: int
