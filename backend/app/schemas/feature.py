from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class CompanyFeatureSnapshotOut(BaseModel):
    snapshot_id: str
    crn: str
    as_of_timestamp: datetime
    created_at: datetime

    # Pillar 1: Financing & Charges
    charges_satisfied_30d: int
    charges_satisfied_90d: int
    charges_satisfied_180d: int
    charges_created_90d: int
    age_latest_charge_satisfaction_days: Optional[float] = None
    latest_charge_scope: Optional[str] = None
    charge_evidence_strength: float

    # Pillar 2: Credit & Payment Risk (DBT vs DSO explicitly segregated)
    dbt_current_days: Optional[float] = None
    dbt_trend_90d: Optional[float] = None
    dbt_data_available: int = 0
    credit_score_current: Optional[float] = None
    credit_score_drop_90d: Optional[float] = None
    credit_score_data_available: int = 0
    adverse_insolvency_events_count: int
    
    # DSO (Strictly separated from DBT)
    dso_current_days: Optional[float] = None
    dso_trend_90d: Optional[float] = None
    dso_data_available: int = 0

    # Pillar 3: Workforce Dynamics
    headcount_current: Optional[int] = None
    headcount_growth_30d_pct: Optional[float] = None
    headcount_growth_90d_pct: Optional[float] = None
    headcount_growth_180d_pct: Optional[float] = None
    headcount_acceleration_pct: Optional[float] = None
    headcount_observation_count: int
    headcount_confidence_score: float
    workforce_data_available: int = 0

    # Pillar 4: Corroboration & Quality
    corroboration_signal_count: int
    distinct_source_count: int
    evidence_coverage: str
    contradictory_evidence_flags: Optional[str] = None

    class Config:
        from_attributes = True
