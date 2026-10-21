import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class RawEvent(Base):
    __tablename__ = "raw_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_provider = Column(String(50), nullable=False, index=True)
    provider_event_id = Column(String(255), nullable=True, index=True)
    payload_hash = Column(String(64), nullable=False, unique=True, index=True)  # Deduplication
    raw_payload = Column(Text, nullable=False)
    processing_status = Column(String(50), default="received", index=True)  # received, processed, dead_letter
    retry_count = Column(Integer, default=0)
    error_details = Column(Text, nullable=True)
    received_at = Column(DateTime, default=utcnow)
    processed_at = Column(DateTime, nullable=True)

class NormalizedSignal(Base):
    __tablename__ = "normalized_signals"

    signal_id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    raw_event_id = Column(String(36), ForeignKey("raw_events.id", ondelete="SET NULL"), nullable=True)
    signal_type = Column(String(50), nullable=False, index=True)
    source_provider = Column(String(50), nullable=False, index=True)
    original_event_ref = Column(String(255), nullable=True)
    observation_timestamp = Column(DateTime, nullable=False, index=True)  # True event time
    ingestion_timestamp = Column(DateTime, default=utcnow)
    
    value_numeric = Column(Float, nullable=True)
    value_text = Column(String(255), nullable=True)
    value_units = Column(String(50), nullable=True)  # 'days', 'score_pts', '%', 'count'
    direction = Column(String(20), default="neutral")  # 'strain_increasing', 'strain_decreasing', 'neutral'
    
    confidence_score = Column(Float, default=1.0)  # Source credibility & extraction confidence
    evidence_reference = Column(Text, nullable=True)  # Official URL / filing ID
    decay_half_life_days = Column(Integer, default=90)  # Signal decay policy
    verification_status = Column(String(50), default="verified", index=True)  # verified, provisional, disputed
    data_use_restrictions = Column(String(255), default="b2b_commercial_finance_only")
    provenance_details = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    company = relationship("CompanyRegistry", back_populates="signals")

    __table_args__ = (
        Index("idx_crn_obs_time", "crn", "observation_timestamp"),
        Index("idx_signal_type_obs", "signal_type", "observation_timestamp"),
    )

class StreamCheckpoint(Base):
    """Durable checkpoint persistence for streaming ingestion (Companies House charges stream)."""
    __tablename__ = "stream_checkpoints"

    stream_name = Column(String(100), primary_key=True)
    last_timepoint = Column(String(255), nullable=True)
    last_processed_at = Column(DateTime, default=utcnow)
    events_processed_count = Column(Integer, default=0)
    status = Column(String(50), default="active")  # active, paused, degraded_gap, error
    has_gap = Column(Boolean, default=False)
    gap_from_timepoint = Column(String(255), nullable=True)
    gap_to_timepoint = Column(String(255), nullable=True)
    gap_detected_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

class DeadLetterEvent(Base):
    """Dead-letter queue for repeatedly failing or unparseable event payloads."""
    __tablename__ = "dead_letter_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_event_id = Column(String(36), nullable=True)
    source_provider = Column(String(50), nullable=False, index=True)
    raw_payload = Column(Text, nullable=False)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)
