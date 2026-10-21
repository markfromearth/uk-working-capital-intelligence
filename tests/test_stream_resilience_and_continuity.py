import json
from unittest.mock import patch, MagicMock
import pytest
from backend.app.database import SessionLocal, init_db
from backend.app.models.signal import StreamCheckpoint, RawEvent, NormalizedSignal
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.services.ingestion import IngestionService

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_stream_416_records_explicit_degraded_gap(db_session):
    """
    Proves that when an HTTP 416 occurs, rather than silently claiming
    healthy ingestion, the system records an explicit gap with 'degraded_gap' status.
    """
    ingestion = IngestionService(db_session)
    stream_name = "test_gap_stream"

    # Set initial checkpoint
    ingestion.update_checkpoint(stream_name, timepoint="5000", processed_count=10)

    # Simulate 416 expired cursor at timepoint 5000; stream head is at 9500
    cp = ingestion.record_stream_gap(
        stream_name=stream_name,
        from_timepoint="5000",
        to_timepoint="9500"
    )
    assert cp.status == "degraded_gap"
    assert cp.has_gap is True
    assert cp.gap_from_timepoint == "5000"
    assert cp.gap_to_timepoint == "9500"
    assert cp.gap_detected_at is not None

def test_checkpoint_cannot_advance_beyond_durably_handled_events(db_session):
    """
    Proves that when processing a batch of stream events, if an intermediate event
    fails, the checkpoint strictly remains at the LAST SUCCESSFULLY COMMITTED event.
    It does NOT skip ahead to subsequent events.
    """
    ingestion = IngestionService(db_session)
    stream_name = "test_atomic_batch_stream"

    events = [
        {
            "timepoint": "1001",
            "event": {
                "timepoint": "1001",
                "published_at": "2026-10-01T10:00:00Z",
                "fields": {
                    "company_number": "08931201",
                    "form_type": "MR04",
                    "status": "fully-satisfied",
                    "description": "Valid satisfied charge"
                }
            }
        },
        {
            "timepoint": "1002",
            "event": {
                "timepoint": "1002",
                "published_at": "2026-10-01T10:05:00Z",
                "fields": {
                    # Malformed event with missing company_number causing processing failure
                    "description": "Missing CRN charge filing"
                }
            }
        },
        {
            "timepoint": "1003",
            "event": {
                "timepoint": "1003",
                "published_at": "2026-10-01T10:10:00Z",
                "fields": {
                    "company_number": "SC123456",
                    "form_type": "MR04",
                    "status": "fully-satisfied",
                    "description": "Another valid charge"
                }
            }
        }
    ]

    # Mock an error when processing timepoint 1002
    original_ingest = ingestion.ingest_raw_event
    def mock_ingest(source_provider, payload, provider_event_id):
        if provider_event_id == "1002":
            raise ValueError("Simulated database constraint failure on event 1002")
        return original_ingest(source_provider, payload, provider_event_id)

    with patch.object(ingestion, "ingest_raw_event", side_effect=mock_ingest):
        success_count, last_tp, err = ingestion.process_stream_batch(stream_name, events)

    # 1001 succeeded, 1002 failed and batch stopped
    assert success_count == 1
    assert last_tp == "1001"
    assert err is not None
    assert "Simulated database constraint failure" in err

    # Checkpoint in database must be at 1001, NEVER 1002 or 1003
    stored_tp = ingestion.get_checkpoint(stream_name)
    assert stored_tp == "1001"

def test_stream_reconnection_deduplicates_events(db_session):
    """
    Proves that duplicate events delivered upon stream reconnection are
    idempotently deduplicated via SHA-256 payload hashing and do not create
    duplicate raw records.
    """
    ingestion = IngestionService(db_session)
    stream_name = "test_reconnect_stream"

    import time
    ts = str(time.time())
    duplicate_event = {
        "timepoint": f"reconnect-{ts}",
        "event": {
            "published_at": "2026-10-01T12:00:00Z",
            "fields": {
                "company_number": "08931201",
                "form_type": "MR04",
                "status": "fully-satisfied",
                "description": f"Reconnection test payload {ts}"
            }
        }
    }

    # First delivery
    raw1, is_new1 = ingestion.ingest_raw_event(
        source_provider=stream_name,
        payload=duplicate_event,
        provider_event_id=f"reconnect-{ts}"
    )
    assert is_new1 is True

    # Second delivery of identical payload
    raw2, is_new2 = ingestion.ingest_raw_event(
        source_provider=stream_name,
        payload=duplicate_event,
        provider_event_id=f"reconnect-{ts}"
    )
    assert is_new2 is False
    assert raw1.id == raw2.id
