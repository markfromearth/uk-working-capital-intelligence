from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class ScoreContributionOut(BaseModel):
    pillar: str
    feature_name: str
    feature_value: Optional[float] = None
    points_awarded: float
    max_pillar_points: float
    explanation: str
    evidence_signal_id: Optional[str] = None

    class Config:
        from_attributes = True

class LiquidityScoreRecordOut(BaseModel):
    id: str
    crn: str
    snapshot_id: Optional[str] = None
    score: float = Field(..., ge=0.0, le=100.0)
    score_band: str
    evidence_coverage: str
    model_version: str
    
    financing_position_score: float
    credit_payment_risk_score: float
    workforce_expansion_score: float
    corroboration_score: float
    
    decay_factor: float
    contradictory_penalty: float
    raw_unclamped_score: float
    
    ai_summary: Optional[str] = None
    ai_model_version: Optional[str] = None
    ai_generated_at: Optional[datetime] = None
    scored_at: datetime
    
    contributions: Optional[List[ScoreContributionOut]] = None

    class Config:
        from_attributes = True

class ScoreRecomputeRequest(BaseModel):
    as_of_timestamp: Optional[datetime] = None
    force_ai_summary: bool = False
