import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Float, ForeignKey, Index
from backend.app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def generate_uuid():
    return str(uuid.uuid4())


class ModelPrediction(Base):
    """
    Persistent log of all point-in-time model predictions and accompanying rules scores.
    Enables post-hoc backtesting, calibration tracking, and promotion validation against real commercial outcomes.
    """
    __tablename__ = "model_predictions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    crn = Column(String(10), ForeignKey("company_registry.crn", ondelete="CASCADE"), nullable=False, index=True)
    as_of = Column(DateTime, nullable=False, index=True)
    model_version = Column(String(50), nullable=False, index=True)
    p_value = Column(Float, nullable=False)
    rules_p_value = Column(Float, nullable=False)
    feature_vector_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        Index("ix_model_predictions_crn_as_of", "crn", "as_of"),
    )
