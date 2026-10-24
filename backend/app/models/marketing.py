"""
SQLAlchemy Models for Marketing Channel Discovery and Commercial Activation.
Stores verified UK podcasts, trade associations, publications, and events.
"""

from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text
from backend.app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class MarketingChannelType(str, Enum):
    PODCAST = "PODCAST"
    TRADE_ASSOCIATION = "TRADE_ASSOCIATION"
    PUBLICATION = "PUBLICATION"
    CONFERENCE = "CONFERENCE"
    COMMUNITY = "COMMUNITY"


class CommercialOpportunityType(str, Enum):
    SPONSORSHIP = "SPONSORSHIP"
    EXHIBITION = "EXHIBITION"
    EDITORIAL_PR = "EDITORIAL_PR"
    GUEST_APPEARANCE = "GUEST_APPEARANCE"
    PARTNERSHIP = "PARTNERSHIP"


class MarketingChannelCandidate(Base):
    __tablename__ = "marketing_channel_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_name = Column(String(255), nullable=False, index=True)
    channel_type = Column(String(50), nullable=False, index=True)
    sector_sic_prefixes = Column(String(100), nullable=False, index=True)  # e.g. "49,50,51,52,53"
    target_audience_role = Column(String(100), default="Finance Director / Managing Director")
    website_uri = Column(Text, nullable=False)
    feed_uri = Column(Text, nullable=True)
    reach_metric_text = Column(String(100), default="UNOBSERVED")  # Explicitly unobserved if not audited
    is_reach_verified = Column(Boolean, default=False)
    commercial_opportunity_type = Column(String(50), default="EDITORIAL_PR")
    estimated_cost_tier = Column(String(50), default="UNKNOWN")  # FREE_EDITORIAL, LOW, MEDIUM, HIGH, UNKNOWN
    confidence_score = Column(Float, default=0.85)
    source_reference = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    last_verified_at = Column(DateTime, default=utcnow)
