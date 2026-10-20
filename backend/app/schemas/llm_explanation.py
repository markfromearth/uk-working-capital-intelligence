from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class SignalReference(BaseModel):
    signal_id: Optional[str] = Field(None, description="Database UUID of the stored signal")
    signal_type: str
    source_provider: str
    observation_date: str
    description: str

class VerificationStep(BaseModel):
    step_number: int
    action_type: str  # e.g. 'companies_house_search', 'credit_report_check', 'broker_client_inquiry'
    recommendation: str
    justification: str

class LLMExplanationContract(BaseModel):
    company_crn: str
    liquidity_strain_score: float = Field(..., ge=0.0, le=100.0)
    score_band: str
    evidence_coverage: str
    primary_signals: List[SignalReference]
    corroborating_signals: List[SignalReference]
    contradictory_evidence: List[str]
    missing_information: List[str]
    confidence_level: str = Field(..., description="'high', 'medium', or 'low'")
    reasoning_summary: str = Field(..., description="Objective, evidence-grounded summary without subjective speculation")
    recommended_verification_steps: List[VerificationStep]
    broker_review_required: bool
    model_version: str
    generated_at: datetime
