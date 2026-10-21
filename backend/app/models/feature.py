import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class CompanyFeatureSnapshot(Base):
    """
    Point-in-time snapshot of derived features for a company.
    Guarantees no data leakage when scoring historically.
    """
    __tablename__ = "company_feature_snapshots"

    snapshot_id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    as_of_timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)

    # Pillar 1: Financing-position & Charge activity
    charges_satisfied_30d = Column(Integer, default=0)
    charges_satisfied_90d = Column(Integer, default=0)
    charges_satisfied_180d = Column(Integer, default=0)
    charges_created_90d = Column(Integer, default=0)
    age_latest_charge_satisfaction_days = Column(Float, nullable=True)
    latest_charge_scope = Column(String(50), nullable=True)  # 'full', 'partial', 'none'
    charge_evidence_strength = Column(Float, default=0.0)

    # Pillar 2: Commercial Credit & Payment Risk (DBT strictly separated from DSO)
    dbt_current_days = Column(Float, nullable=True)  # Days Beyond Terms
    dbt_trend_90d = Column(Float, nullable=True)     # Change in DBT
    dbt_data_available = Column(Integer, default=0)
    credit_score_current = Column(Float, nullable=True)
    credit_score_drop_90d = Column(Float, nullable=True)
    credit_score_data_available = Column(Integer, default=0)
    adverse_insolvency_events_count = Column(Integer, default=0)
    
    # DSO (Only populated when genuine receivables observation exists)
    dso_current_days = Column(Float, nullable=True)  # Genuine DSO if available
    dso_trend_90d = Column(Float, nullable=True)
    dso_data_available = Column(Integer, default=0)  # 0 if missing/unobserved

    # Pillar 3: Workforce & Headcount Dynamics
    headcount_current = Column(Integer, nullable=True)
    headcount_growth_30d_pct = Column(Float, nullable=True)
    headcount_growth_90d_pct = Column(Float, nullable=True)
    headcount_growth_180d_pct = Column(Float, nullable=True)
    headcount_acceleration_pct = Column(Float, nullable=True)
    headcount_observation_count = Column(Integer, default=0)
    headcount_confidence_score = Column(Float, default=0.0)
    workforce_data_available = Column(Integer, default=0)

    # Pillar 4: Corroboration & Quality
    corroboration_signal_count = Column(Integer, default=0)
    distinct_source_count = Column(Integer, default=0)
    evidence_coverage = Column(String(50), default="limited")  # limited, emerging, elevated, strong
    contradictory_evidence_flags = Column(Text, nullable=True)  # JSON list of benign indicators

    company = relationship("CompanyRegistry", back_populates="feature_snapshots")

    __table_args__ = (
        Index("idx_snapshot_crn_asof", "crn", "as_of_timestamp"),
    )
