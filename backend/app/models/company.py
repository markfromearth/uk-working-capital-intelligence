from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class CompanyRegistry(Base):
    __tablename__ = "company_registry"

    crn = Column(String(10), primary_key=True, index=True)  # Canonical UK CRN
    company_name = Column(String(255), nullable=False, index=True)
    company_status = Column(String(50), default="active", index=True)
    company_type = Column(String(50), nullable=True)
    incorporation_date = Column(DateTime, nullable=True)
    registered_office_address = Column(Text, nullable=True)
    sic_codes = Column(Text, nullable=True)  # Comma-separated or JSON
    website_domain = Column(String(255), nullable=True)
    source_reference = Column(String(255), nullable=True)
    is_synthetic = Column(Integer, default=0)  # 1 if seeded demonstration record
    verified_at = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    previous_names = relationship("CompanyPreviousName", back_populates="company", cascade="all, delete-orphan")
    signals = relationship("NormalizedSignal", back_populates="company", cascade="all, delete-orphan")
    scores = relationship("LiquidityScoreRecord", back_populates="company", cascade="all, delete-orphan")
    feature_snapshots = relationship("CompanyFeatureSnapshot", back_populates="company", cascade="all, delete-orphan")
    reviews = relationship("BrokerReviewAction", back_populates="company", cascade="all, delete-orphan")

class CompanyPreviousName(Base):
    __tablename__ = "company_previous_names"

    id = Column(Integer, primary_key=True, autoincrement=True)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    effective_from = Column(DateTime, nullable=True)
    ceased_on = Column(DateTime, nullable=True)

    company = relationship("CompanyRegistry", back_populates="previous_names")

class CompanyMatchReview(Base):
    """Review queue for ambiguous or conflicting entity resolution matches."""
    __tablename__ = "company_match_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_identifier = Column(String(255), nullable=False, index=True)
    candidate_crn = Column(String(10), nullable=True, index=True)
    candidate_name = Column(String(255), nullable=True)
    source_provider = Column(String(50), nullable=False)
    match_score = Column(Float, nullable=False)
    resolution_method = Column(String(50), nullable=False)  # exact, deterministic_fuzzy, manual
    status = Column(String(50), default="pending", index=True)  # pending, approved, rejected
    reviewer_notes = Column(Text, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    raw_payload_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    reviewed_at = Column(DateTime, nullable=True)
