import json
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.signal import DeadLetterEvent, NormalizedSignal
from backend.app.services.ingestion import IngestionService

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_dead_letter_replay_lifecycle(db_session):
    ingestion = IngestionService(db_session)

    # 1. Route a recoverable signal payload to Dead Letter Queue
    payload = {
        "crn": "08931201",
        "signal_type": "dbt_increase",
        "source_provider": "creditsafe",
        "original_event_ref": "dlq-test-evt-001",
        "observation_timestamp": "2026-09-10T12:00:00Z",
        "value_numeric": 22.5,
        "value_text": "22.5 days DBT",
        "direction": "strain_increasing"
    }
    dle = ingestion.route_to_dead_letter(
        source_provider="creditsafe",
        payload=payload,
        error_msg="Temporary processing timeout"
    )
    assert dle.id is not None
    assert dle.resolved_at is None

    # 2. Replay via API endpoint
    response = client.post(f"/api/v1/ingest/dead-letter-queue/{dle.id}/replay")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "replayed"
    assert res_data["dead_letter_id"] == dle.id

    # 3. Verify in database
    db_session.refresh(dle)
    assert dle.resolved_at is not None
