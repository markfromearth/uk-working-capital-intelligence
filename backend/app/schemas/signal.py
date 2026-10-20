from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class NormalizedSignalBase(BaseModel):
    crn: str
    signal_type: str
    source_provider: str
    original_event_ref: Optional[str] = None
    observation_timestamp: datetime
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    value_units: Optional[str] = None
    direction: Optional[str] = "neutral"
    confidence_score: Optional[float] = 1.0
    evidence_reference: Optional[str] = None
    decay_half_life_days: Optional[int] = 90
    verification_status: Optional[str] = "verified"
    metadata_json: Optional[str] = None

class NormalizedSignalCreate(NormalizedSignalBase):
    raw_event_id: Optional[str] = None

class NormalizedSignalOut(NormalizedSignalBase):
    signal_id: str
    ingestion_timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True

class RawEventIngest(BaseModel):
    source_provider: str
    provider_event_id: Optional[str] = None
    payload: Dict[str, Any]

class StreamCheckpointOut(BaseModel):
    stream_name: str
    last_timepoint: Optional[str] = None
    last_processed_at: datetime
    events_processed_count: int
    status: str

    class Config:
        from_attributes = True
