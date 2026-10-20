import hashlib
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.signal import RawEvent, NormalizedSignal, StreamCheckpoint, DeadLetterEvent
from backend.app.models.company import CompanyRegistry

def calculate_payload_hash(payload: Any) -> str:
    """Computes deterministic SHA-256 hash of a payload dictionary or string for deduplication."""
    if isinstance(payload, dict):
        canonical_str = json.dumps(payload, sort_keys=True, default=str)
    else:
        canonical_str = str(payload)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

class IngestionService:
    """
    Handles robust, idempotent event ingestion, deduplication, normalisation,
    and dead-letter queue routing.
    """

    def __init__(self, db: Session):
        self.db = db

    def ingest_raw_event(
        self,
        source_provider: str,
        payload: Dict[str, Any],
        provider_event_id: Optional[str] = None
    ) -> Tuple[RawEvent, bool]:
        """
        Idempotently ingests a raw event payload.
        Returns: (RawEvent, is_new_event)
        """
        payload_hash = calculate_payload_hash(payload)
        
        # Deduplication check
        existing = self.db.query(RawEvent).filter(RawEvent.payload_hash == payload_hash).first()
        if existing:
            logger.info(f"Deduplicated event {existing.id} from {source_provider} (hash={payload_hash[:12]})")
            return existing, False

        raw_event = RawEvent(
            source_provider=source_provider,
            provider_event_id=provider_event_id,
            payload_hash=payload_hash,
            raw_payload=json.dumps(payload, default=str),
            processing_status="received",
            received_at=datetime.now(timezone.utc)
        )
        self.db.add(raw_event)
        self.db.commit()
        self.db.refresh(raw_event)
        return raw_event, True

    def process_normalized_signal(self, raw_event_id: Optional[str], signal_data: Dict[str, Any]) -> Optional[NormalizedSignal]:
        """
        Normalises and stores a signal tied to a registered company CRN.
        """
        raw_crn = signal_data.get("crn")
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            logger.warning(f"Rejected signal due to invalid CRN: {raw_crn} - {err}")
            self.route_to_dead_letter(
                source_provider=signal_data.get("source_provider", "unknown"),
                payload=signal_data,
                error_msg=f"Invalid CRN: {err}",
                raw_event_id=raw_event_id
            )
            return None

        # Ensure company registry entry exists or create placeholder
        company = self.db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
        if not company:
            company = CompanyRegistry(
                crn=norm_crn,
                company_name=f"Company {norm_crn}",
                company_status="active",
                created_at=datetime.now(timezone.utc)
            )
            self.db.add(company)
            self.db.commit()

        # Check for duplicate signal on the same CRN, signal_type, and observation date
        obs_dt = signal_data.get("observation_timestamp")
        if isinstance(obs_dt, str):
            obs_dt = datetime.fromisoformat(obs_dt.replace("Z", "+00:00"))

        existing_sig = self.db.query(NormalizedSignal).filter(
            NormalizedSignal.crn == norm_crn,
            NormalizedSignal.signal_type == signal_data.get("signal_type"),
            NormalizedSignal.original_event_ref == signal_data.get("original_event_ref")
        ).first()

        if existing_sig:
            logger.debug(f"Signal already exists: {norm_crn} / {signal_data.get('signal_type')}")
            return existing_sig

        signal = NormalizedSignal(
            crn=norm_crn,
            raw_event_id=raw_event_id,
            signal_type=signal_data.get("signal_type"),
            source_provider=signal_data.get("source_provider"),
            original_event_ref=signal_data.get("original_event_ref"),
            observation_timestamp=obs_dt,
            value_numeric=signal_data.get("value_numeric"),
            value_text=signal_data.get("value_text"),
            value_units=signal_data.get("value_units"),
            direction=signal_data.get("direction", "neutral"),
            confidence_score=signal_data.get("confidence_score", 1.0),
            evidence_reference=signal_data.get("evidence_reference"),
            decay_half_life_days=signal_data.get("decay_half_life_days", 90),
            verification_status=signal_data.get("verification_status", "verified"),
            metadata_json=signal_data.get("metadata_json")
        )
        self.db.add(signal)
        
        # Mark raw event as processed if linked
        if raw_event_id:
            raw_event = self.db.query(RawEvent).filter(RawEvent.id == raw_event_id).first()
            if raw_event:
                raw_event.processing_status = "processed"
                raw_event.processed_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(signal)
        return signal

    def route_to_dead_letter(
        self,
        source_provider: str,
        payload: Any,
        error_msg: str,
        raw_event_id: Optional[str] = None,
        exc: Optional[Exception] = None
    ) -> DeadLetterEvent:
        """Saves unparseable or repeatedly failing events to dead-letter storage."""
        tb = traceback.format_exc() if exc else None
        dle = DeadLetterEvent(
            raw_event_id=raw_event_id,
            source_provider=source_provider,
            raw_payload=json.dumps(payload, default=str) if isinstance(payload, dict) else str(payload),
            error_message=error_msg,
            stack_trace=tb,
            created_at=datetime.now(timezone.utc)
        )
        self.db.add(dle)
        if raw_event_id:
            raw_event = self.db.query(RawEvent).filter(RawEvent.id == raw_event_id).first()
            if raw_event:
                raw_event.processing_status = "dead_letter"
                raw_event.error_details = error_msg
        self.db.commit()
        self.db.refresh(dle)
        logger.error(f"Event routed to Dead Letter Queue [ID={dle.id}]: {error_msg}")
        return dle

    # --- Durable Stream Checkpoint Management ---

    def get_checkpoint(self, stream_name: str) -> Optional[str]:
        cp = self.db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        return cp.last_timepoint if cp else None

    def update_checkpoint(self, stream_name: str, timepoint: str, processed_count: int = 1) -> StreamCheckpoint:
        cp = self.db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        now = datetime.now(timezone.utc)
        if not cp:
            cp = StreamCheckpoint(
                stream_name=stream_name,
                last_timepoint=timepoint,
                last_processed_at=now,
                events_processed_count=processed_count,
                status="active"
            )
            self.db.add(cp)
        else:
            cp.last_timepoint = timepoint
            cp.last_processed_at = now
            cp.events_processed_count += processed_count
            cp.status = "active"
        self.db.commit()
        self.db.refresh(cp)
        return cp
