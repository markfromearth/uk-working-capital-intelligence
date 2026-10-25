import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.signal import RawEvent, NormalizedSignal, DeadLetterEvent
from backend.app.models.user import User
from backend.app.api.v1.auth import require_role
from backend.app.services.ingestion import IngestionService
from backend.app.providers.registry import provider_registry

router = APIRouter(prefix="/ingest", tags=["Signal Ingestion & Webhooks"])

@router.post("/webhooks/creditsafe")
async def creditsafe_webhook_receiver(
    request: Request,
    x_creditsafe_signature: str = Header(None, alias="X-Creditsafe-Signature"),
    x_creditsafe_timestamp: Optional[str] = Header(None, alias="X-Creditsafe-Timestamp"),
    db: Session = Depends(get_db)
):
    """
    Authenticated Webhook Receiver for Creditsafe Commercial Risk Events.
    Fails closed when CREDITSAFE_WEBHOOK_SECRET is unconfigured.
    Verifies HMAC signature, enforces replay protection, deduplicates,
    and ingests signals idempotently.
    """
    if not settings.CREDITSAFE_WEBHOOK_SECRET:
        logger.warning("Rejected Creditsafe webhook: CREDITSAFE_WEBHOOK_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook authentication secret is not configured; unsigned webhooks are rejected"
        )

    body_bytes = await request.body()
    adapter = provider_registry.creditsafe

    if not adapter.verify_webhook(body_bytes, x_creditsafe_signature, timestamp_header=x_creditsafe_timestamp):
        logger.warning("Rejected Creditsafe webhook: Invalid HMAC signature or timestamp replay")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature or timestamp drift"
        )

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        logger.error(f"Malformed JSON in webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    ingestion_service = IngestionService(db)
    event_id = payload.get("notification_id") or payload.get("event_id")
    raw_event, is_new = ingestion_service.ingest_raw_event(
        source_provider="creditsafe",
        payload=payload,
        provider_event_id=event_id
    )

    if not is_new:
        return {"status": "deduplicated", "raw_event_id": raw_event.id, "message": "Event already processed"}

    # Parse and normalise signals
    parsed_signals = adapter.parse_webhook_payload(payload)
    created_signals = []
    for sig_data in parsed_signals:
        sig = ingestion_service.process_normalized_signal(raw_event.id, sig_data)
        if sig:
            created_signals.append(sig.signal_id)

    return {
        "status": "accepted",
        "raw_event_id": raw_event.id,
        "signals_created_count": len(created_signals),
        "signal_ids": created_signals
    }

@router.post("/sync/companies-house-stream")
def trigger_companies_house_stream_sync(
    limit: int = 20,
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst"])),
    db: Session = Depends(get_db)
):
    """
    Polls Companies House charges stream starting from the last durable checkpoint.
    Updates checkpoint monotonically and ingests charge filings into signals.
    """
    ingestion_service = IngestionService(db)
    stream_name = "companies_house_charges_stream"
    checkpoint = ingestion_service.get_checkpoint(stream_name)

    adapter = provider_registry.companies_house
    stream_result = adapter.poll_stream_events(timepoint=checkpoint, limit=limit)

    events = stream_result.get("events", [])
    next_timepoint = stream_result.get("next_timepoint")

    processed_count = 0
    for evt in events:
        chg_event = evt.get("event", {})
        fields = chg_event.get("fields", {})
        crn = fields.get("company_number")
        if not crn:
            continue

        raw_evt, is_new = ingestion_service.ingest_raw_event(
            source_provider="companies_house",
            payload=evt,
            provider_event_id=str(evt.get("timepoint"))
        )

        if is_new:
            # Map into signals
            chg_dict = {
                "id": f"chg-{crn}-{evt.get('timepoint')}",
                "status": fields.get("status", "fully-satisfied"),
                "satisfied_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "classification": {"description": fields.get("description", "Debenture Charge")},
                "persons_entitled": []
            }
            signals = adapter._parse_charge_into_signals(crn, chg_dict)
            for s in signals:
                ingestion_service.process_normalized_signal(raw_evt.id, s)
            processed_count += 1

    # Advance checkpoint
    if next_timepoint and next_timepoint != checkpoint:
        ingestion_service.update_checkpoint(stream_name, str(next_timepoint), processed_count=len(events))

    return {
        "stream_name": stream_name,
        "previous_timepoint": checkpoint,
        "next_timepoint": next_timepoint,
        "events_received": len(events),
        "events_processed": processed_count
    }

@router.post("/dead-letter-queue/{dlq_id}/replay")
def replay_dead_letter_queue_event(
    dlq_id: int,
    current_user: User = Depends(require_role(["ADMIN", "admin"])),
    db: Session = Depends(get_db)
):
    """
    Attempts to replay and reprocess a quarantined Dead Letter Queue event.
    Restricted to Admin role.
    """
    ingestion_service = IngestionService(db)
    success, message = ingestion_service.replay_dead_letter_event(dlq_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"status": "replayed", "message": message, "dead_letter_id": dlq_id}

@router.get("/dead-letter-queue")
def list_dead_letter_events(
    limit: int = 50,
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst"])),
    db: Session = Depends(get_db)
):
    """Inspect unparseable or rejected event payloads. Restricted to Admin and Analyst roles."""
    dles = db.query(DeadLetterEvent).order_by(DeadLetterEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "id": d.id,
            "source_provider": d.source_provider,
            "error_message": d.error_message,
            "raw_payload": d.raw_payload[:200] + "..." if len(d.raw_payload) > 200 else d.raw_payload,
            "retry_count": d.retry_count,
            "created_at": d.created_at
        } for d in dles
    ]
