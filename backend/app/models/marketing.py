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


class CampaignTrackingRecord(Base):
    __tablename__ = "campaign_tracking_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String(50), unique=True, nullable=False, index=True)
    strategy_id = Column(String(50), nullable=True, index=True)
    crn = Column(String(12), nullable=True, index=True)
    channel_name = Column(String(255), nullable=False)
    channel_type = Column(String(50), nullable=False)
    content_format = Column(String(50), nullable=False)
    utm_campaign = Column(String(100), nullable=False)
    utm_source = Column(String(100), nullable=False)
    utm_medium = Column(String(100), nullable=False)
    budget_gbp = Column(Float, default=0.0)
    status = Column(String(50), default="ACTIVE")
    owner_user_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class CampaignOutcomeEvent(Base):
    __tablename__ = "campaign_outcome_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String(50), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # IMPRESSION, CLICK, QUALIFIED_ENQUIRY, APPLICATION, FUNDED_DEAL
    crn = Column(String(12), nullable=True, index=True)
    funded_amount_gbp = Column(Float, default=0.0)
    realized_commission_gbp = Column(Float, default=0.0)
    event_timestamp = Column(DateTime, nullable=False, default=utcnow)
    source_reference = Column(Text, nullable=True)
    event_fingerprint = Column(String(64), nullable=True, unique=True, index=True)
    recorded_by_user_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)


