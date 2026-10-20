import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class BrokerReviewAction(Base):
    __tablename__ = "broker_review_actions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_broker_name = Column(String(100), nullable=True)
    
    review_status = Column(String(50), default="unreviewed", index=True)  
    # 'unreviewed', 'in_review', 'verified_opportunity', 'suppressed', 'snoozed', 'disputed_signal'
    
    signal_id_disputed = Column(String(36), nullable=True)
    dispute_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    
    verification_request = Column(Text, nullable=True)
    snooze_until = Column(DateTime, nullable=True)
    
    # Ground truth outcome tracking for model calibration
    outcome = Column(String(50), nullable=True)  
    # 'funded_refinance', 'active_discussion', 'no_funding_need', 'unreachable', 'existing_facility'
    outcome_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    company = relationship("CompanyRegistry", back_populates="reviews")
