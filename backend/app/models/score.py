import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class LiquidityScoreRecord(Base):
    __tablename__ = "liquidity_score_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(String(36), ForeignKey("company_feature_snapshots.snapshot_id", ondelete="SET NULL"), nullable=True)
    
    score = Column(Float, nullable=False, index=True)  # 0.0 to 100.0
    score_band = Column(String(50), nullable=False, index=True)  # limited, emerging, elevated, strong
    evidence_coverage = Column(String(50), nullable=False)  # limited, emerging, elevated, strong
    model_version = Column(String(20), default="v1.0.0", nullable=False)
    
    # Pillar Sub-Scores
    financing_position_score = Column(Float, default=0.0)  # max 25
    credit_payment_risk_score = Column(Float, default=0.0) # max 35
    workforce_expansion_score = Column(Float, default=0.0) # max 20
    corroboration_score = Column(Float, default=0.0)       # max 20
    
    # Dampening & Caps
    decay_factor = Column(Float, default=1.0)
    contradictory_penalty = Column(Float, default=0.0)
    raw_unclamped_score = Column(Float, default=0.0)
    
    # AI Summary Cache
    ai_summary = Column(Text, nullable=True)
    ai_model_version = Column(String(50), nullable=True)
    ai_generated_at = Column(DateTime, nullable=True)
    
    scored_at = Column(DateTime, default=utcnow, index=True)

    company = relationship("CompanyRegistry", back_populates="scores")
    contributions = relationship("ScoreContributionRecord", back_populates="score_record", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_score_crn_time", "crn", "scored_at"),
        Index("idx_score_val_band", "score", "score_band"),
    )

class ScoreContributionRecord(Base):
    """Granular feature-level contribution to the liquidity score for full explainability."""
    __tablename__ = "score_contribution_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    score_record_id = Column(String(36), ForeignKey("liquidity_score_records.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar = Column(String(50), nullable=False)  # financing, credit, workforce, corroboration
    feature_name = Column(String(100), nullable=False)
    feature_value = Column(Float, nullable=True)
    points_awarded = Column(Float, nullable=False)
    max_pillar_points = Column(Float, nullable=False)
    explanation = Column(Text, nullable=False)
    evidence_signal_id = Column(String(36), nullable=True)

    score_record = relationship("LiquidityScoreRecord", back_populates="contributions")

class ScoreCalibrationConfig(Base):
    """Dynamic versioned scoring calibration parameters."""
    __tablename__ = "score_calibration_configs"

    version = Column(String(20), primary_key=True)  # e.g. "v1.0.0"
    max_financing_pts = Column(Float, default=25.0)
    max_credit_pts = Column(Float, default=35.0)
    max_workforce_pts = Column(Float, default=20.0)
    max_corroboration_pts = Column(Float, default=20.0)
    signal_half_life_days = Column(Integer, default=90)
    min_evidence_for_high_band = Column(Integer, default=2)
    is_active = Column(Integer, default=1)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
