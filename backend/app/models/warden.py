"""Persistent store for Companies House Warden screenings, product matches and dossiers.

Survives process restarts / serverless cold starts so the lead pipeline is served
from the database rather than re-querying Companies House on every request.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, Index

from backend.app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class WardenScreeningRecord(Base):
    __tablename__ = "warden_screenings"

    crn = Column(String(10), primary_key=True, index=True)
    company_name = Column(String(255), nullable=False, index=True)
    company_status = Column(String(50), nullable=True)
    sic_codes = Column(Text, nullable=True)  # comma-separated
    is_b2b = Column(Integer, default=0, index=True)
    knockout_applied = Column(Integer, default=0)
    knockout_reason = Column(Text, nullable=True)
    provisional_score = Column(Float, default=0.0, index=True)
    refined_score = Column(Float, nullable=True)
    surfaced = Column(Integer, default=0, index=True)
    indicator_count = Column(Integer, default=0, index=True)
    triggered_indicators = Column(Text, nullable=True)  # JSON list
    matched_products = Column(Text, nullable=True)  # JSON list
    product_codes = Column(Text, nullable=True)  # ",CODE1,CODE2," for LIKE filtering
    charges_summary = Column(Text, nullable=True)  # JSON
    eligible_book_debts = Column(Float, nullable=True)
    indicative_facility = Column(Float, nullable=True)
    debtors_to_assets_ratio = Column(Integer, nullable=True)
    screening_json = Column(Text, nullable=True)
    dossier_json = Column(Text, nullable=True)
    discovery_source = Column(String(100), nullable=True)
    screened_at = Column(DateTime, default=utcnow, onupdate=utcnow, index=True)

    __table_args__ = (Index("idx_warden_surfaced_score", "surfaced", "indicator_count"),)
