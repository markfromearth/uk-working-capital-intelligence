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
            if signal_data.get("data_use_restrictions"):
                existing_sig.data_use_restrictions = signal_data.get("data_use_restrictions")
            if signal_data.get("provenance_details"):
                existing_sig.provenance_details = signal_data.get("provenance_details")
            self.db.commit()
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
            data_use_restrictions=signal_data.get("data_use_restrictions", "b2b_commercial_finance_only"),
            provenance_details=signal_data.get("provenance_details"),
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

    def replay_dead_letter_event(self, dead_letter_id: int) -> Tuple[bool, str]:
        """
        Attempts to replay a quarantined dead-letter event.
        If reprocessing succeeds, marks resolved_at and updates the associated raw event.
        """
        dle = self.db.query(DeadLetterEvent).filter(DeadLetterEvent.id == dead_letter_id).first()
        if not dle:
            return False, f"Dead letter event {dead_letter_id} not found"

        if dle.resolved_at:
            return True, f"Event {dead_letter_id} already successfully resolved"

        try:
            payload = json.loads(dle.raw_payload)
        except Exception as e:
            dle.retry_count += 1
            self.db.commit()
            return False, f"Cannot replay: Payload is malformed JSON ({e})"

        # Attempt to re-extract and process signal
        try:
            signal = self.process_normalized_signal(dle.raw_event_id, payload)
            if signal:
                dle.resolved_at = datetime.now(timezone.utc)
                self.db.commit()
                logger.info(f"Successfully replayed and resolved Dead Letter Event [ID={dle.id}]")
                return True, f"Successfully reprocessed signal {signal.signal_id}"
            else:
                dle.retry_count += 1
                self.db.commit()
                return False, "Signal could not be processed during replay"
        except Exception as exc:
            dle.retry_count += 1
            dle.error_message = f"Replay attempt failed: {exc}"
            self.db.commit()
            return False, str(exc)

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
            if cp.status != "degraded_gap":
                cp.status = "active"
        self.db.commit()
        self.db.refresh(cp)
        return cp

    def record_stream_gap(
        self,
        stream_name: str,
        from_timepoint: Optional[str],
        to_timepoint: Optional[str]
    ) -> StreamCheckpoint:
        """
        Explicitly records that an unrecoverable gap occurred in the stream
        (e.g. cursor expired with HTTP 416, falling outside provider's stream buffer).
        Sets status='degraded_gap' so monitoring and health checks truthfully report
        the ingestion stream as degraded rather than silently claiming healthy ingestion.
        """
        cp = self.db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        now = datetime.now(timezone.utc)
        if not cp:
            cp = StreamCheckpoint(
                stream_name=stream_name,
                last_timepoint=to_timepoint,
                last_processed_at=now,
                events_processed_count=0,
                status="degraded_gap",
                has_gap=True,
                gap_from_timepoint=from_timepoint,
                gap_to_timepoint=to_timepoint,
                gap_detected_at=now
            )
            self.db.add(cp)
        else:
            cp.status = "degraded_gap"
            cp.has_gap = True
            cp.gap_from_timepoint = from_timepoint
            cp.gap_to_timepoint = to_timepoint
            cp.gap_detected_at = now
            cp.last_timepoint = to_timepoint
        self.db.commit()
        self.db.refresh(cp)
        logger.warning(
            f"STREAM GAP RECORDED on '{stream_name}': from cursor {from_timepoint} to {to_timepoint}. "
            "Status transitioned to 'degraded_gap'."
        )
        return cp

    def process_stream_batch(
        self,
        stream_name: str,
        events: List[Dict[str, Any]]
    ) -> Tuple[int, Optional[str], Optional[str]]:
        """
        Durably processes a batch of stream events.
        Guarantees that the stream checkpoint only advances to the timepoint
        of the LAST SUCCESSFULLY PROCESSED event. If processing fails midway,
        the checkpoint remains at the last good event and cannot advance beyond durably handled data.
        Returns: (success_count, last_successful_timepoint, error_message_if_any)
        """
        success_count = 0
        last_success_tp = None

        for evt in events:
            tp = str(evt.get("timepoint") or evt.get("event", {}).get("timepoint") or "")
            try:
                raw_evt, is_new = self.ingest_raw_event(
                    source_provider=stream_name,
                    payload=evt,
                    provider_event_id=tp
                )
                fields = evt.get("event", {}).get("fields", {})
                crn = fields.get("company_number")
                if crn:
                    sig_payload = {
                        "crn": crn,
                        "source_provider": stream_name,
                        "signal_type": "charge_satisfaction_mr04" if fields.get("form_type") == "MR04" else "charge_filing",
                        "original_event_ref": f"{stream_name}-{tp}",
                        "observation_timestamp": evt.get("event", {}).get("published_at", datetime.now(timezone.utc).isoformat()),
                        "value_text": fields.get("description", "Charge filing"),
                        "direction": "strain_decreasing" if fields.get("status") == "fully-satisfied" else "neutral",
                        "metadata_json": json.dumps({"scope": "full" if fields.get("status") == "fully-satisfied" else "partial", "timepoint": tp})
                    }
                    self.process_normalized_signal(raw_evt.id, sig_payload)

                # Event was durably committed! Advance checkpoint up to this event
                success_count += 1
                last_success_tp = tp
                self.update_checkpoint(stream_name, tp, processed_count=1)

            except Exception as e:
                logger.error(f"Failed processing stream event at timepoint {tp}: {e}")
                return success_count, last_success_tp, str(e)

        return success_count, last_success_tp, None

