import json
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.models.signal import DeadLetterEvent, NormalizedSignal
from backend.app.services.ingestion import IngestionService
from backend.app.core.security import hash_password, create_access_token

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_dead_letter_replay_lifecycle(db_session):
    ingestion = IngestionService(db_session)

    # Provision admin and broker users for RBAC testing
    admin_user = db_session.query(User).filter(User.email == "admin_dlq@test.gov.uk").first()
    if not admin_user:
        admin_user = User(
            email="admin_dlq@test.gov.uk",
            hashed_password=hash_password("AdminSecure2026!"),
            full_name="Admin DLQ Officer",
            role="admin",
            is_active=1
        )
        db_session.add(admin_user)
        db_session.commit()
        db_session.refresh(admin_user)

    broker_user = db_session.query(User).filter(User.email == "broker_dlq@test.co.uk").first()
    if not broker_user:
        broker_user = User(
            email="broker_dlq@test.co.uk",
            hashed_password=hash_password("BrokerSecure2026!"),
            full_name="Broker Regular",
            role="broker",
            is_active=1
        )
        db_session.add(broker_user)
        db_session.commit()
        db_session.refresh(broker_user)

    admin_token = create_access_token(subject=admin_user.id, role="admin")
    broker_token = create_access_token(subject=broker_user.id, role="broker")

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

    # 2. RBAC check: Unauthenticated access rejected with 401
    unauth_resp = client.post(f"/api/v1/ingest/dead-letter-queue/{dle.id}/replay")
    assert unauth_resp.status_code == 401

    # 3. RBAC check: Ordinary broker role access rejected with 403
    broker_resp = client.post(
        f"/api/v1/ingest/dead-letter-queue/{dle.id}/replay",
        headers={"Authorization": f"Bearer {broker_token}"}
    )
    assert broker_resp.status_code == 403

    # 4. Admin authorized access succeeds
    admin_resp = client.post(
        f"/api/v1/ingest/dead-letter-queue/{dle.id}/replay",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert admin_resp.status_code == 200
    res_data = admin_resp.json()
    assert res_data["status"] == "replayed"
    assert res_data["dead_letter_id"] == dle.id

    # 5. Verify in database
    db_session.refresh(dle)
    assert dle.resolved_at is not None
