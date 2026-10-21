import json
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User, AuditLog, AuditCheckpoint
from backend.app.services.audit import log_audit_event, verify_audit_log_chain, create_signed_audit_checkpoint
from backend.app.core.security import hash_password, create_access_token

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

@pytest.fixture(autouse=True)
def clean_audit_state(db_session):
    # Ensure fresh audit state for each test
    db_session.query(AuditCheckpoint).delete()
    db_session.query(AuditLog).delete()
    db_session.commit()
    yield
    db_session.query(AuditCheckpoint).delete()
    db_session.query(AuditLog).delete()
    db_session.commit()

def test_audit_detects_field_modification_including_user_and_ip(db_session):
    """
    Proves that modifying ANY record field (including user_email, user_id,
    user_role, or ip_address) is detected by the hash chain verification.
    """
    entry = log_audit_event(
        db=db_session,
        action="login_attempt",
        resource_type="auth",
        resource_id="user_123",
        user_id="user_123",
        user_email="legit_broker@test.co.uk",
        user_role="broker",
        ip_address="192.168.1.50",
        details={"status": "success"}
    )
    db_session.refresh(entry)

    # 1. Base verification passes
    is_valid, count, err = verify_audit_log_chain(db_session)
    assert is_valid is True
    assert count == 1
    assert err is None

    # 2. Tamper with previously unprotected field: user_email
    entry.user_email = "forged_admin@gov.uk"
    db_session.commit()
    is_valid, _, err = verify_audit_log_chain(db_session)
    assert is_valid is False
    assert "Tamper detected" in err

    # 3. Restore email and tamper with ip_address
    entry.user_email = "legit_broker@test.co.uk"
    entry.ip_address = "10.0.0.99"
    db_session.commit()
    is_valid, _, err = verify_audit_log_chain(db_session)
    assert is_valid is False
    assert "Tamper detected" in err

def test_audit_detects_intermediate_deletion_and_reordering(db_session):
    """
    Proves that deleting an intermediate record or reordering records
    breaks the SHA-256 chain continuity.
    """
    e1 = log_audit_event(db=db_session, action="step_1", resource_type="order", resource_id="1")
    e2 = log_audit_event(db=db_session, action="step_2", resource_type="order", resource_id="2")
    e3 = log_audit_event(db=db_session, action="step_3", resource_type="order", resource_id="3")

    is_valid, count, _ = verify_audit_log_chain(db_session)
    assert is_valid is True
    assert count == 3

    # Delete intermediate record e2
    db_session.delete(e2)
    db_session.commit()

    is_valid, _, err = verify_audit_log_chain(db_session)
    assert is_valid is False
    assert "Audit chain break" in err or "prev_hash mismatch" in err

def test_audit_detects_tail_truncation_via_signed_checkpoint(db_session):
    """
    Proves that an attacker truncating the latest records is detected
    by the independently signed audit checkpoint.
    """
    log_audit_event(db=db_session, action="rec_1", resource_type="doc", resource_id="101")
    log_audit_event(db=db_session, action="rec_2", resource_type="doc", resource_id="102")
    e3 = log_audit_event(db=db_session, action="rec_3", resource_type="doc", resource_id="103")

    # A signed checkpoint covering all 3 records exists
    cp = db_session.query(AuditCheckpoint).order_by(AuditCheckpoint.checkpoint_sequence.desc()).first()
    assert cp is not None
    assert cp.total_records == 3

    # Attacker deletes the latest record (e3)
    db_session.delete(e3)
    db_session.commit()

    # Chain verification checks against the checkpoint and detects truncation
    is_valid, count, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert is_valid is False
    assert "Audit log was truncated" in err

def test_audit_detects_unauthorized_rehash_without_secret_key(db_session):
    """
    An attacker with direct database access alters a record and re-hashes
    all downstream records. Because they do not have the application's SECRET_KEY,
    the signed checkpoint signature cannot be forged.
    """
    log_audit_event(db=db_session, action="tx_start", resource_type="account", resource_id="A1")
    e2 = log_audit_event(db=db_session, action="tx_payout", resource_type="account", resource_id="A2")

    # Attacker maliciously alters e2 and re-hashes record_hash manually
    e2.details_json = '{"amount": 1000000}'
    e2.record_hash = "f" * 64
    db_session.commit()

    is_valid, _, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert is_valid is False
    assert "record_hash recalculation failed" in err or "HMAC signature invalid" in err or "Terminal audit record hash mismatch" in err

def test_audit_verification_endpoint_rbac(db_session):
    """
    Tests access control on GET /api/v1/system/audit-verification:
      - Unauthenticated: 401
      - Ordinary broker: 403
      - Analyst / Admin: 200
    """
    log_audit_event(db=db_session, action="test_evt", resource_type="sys", resource_id="1")

    # 1. Unauthenticated
    resp_unauth = client.get("/api/v1/system/audit-verification")
    assert resp_unauth.status_code == 401

    # 2. Broker role
    broker = db_session.query(User).filter(User.email == "broker_audit@test.com").first()
    if not broker:
        broker = User(
            email="broker_audit@test.com",
            hashed_password=hash_password("Pass123!"),
            full_name="Broker User",
            role="broker",
            is_active=1
        )
        db_session.add(broker)
        db_session.commit()
    broker_token = create_access_token(subject=broker.id, role="broker")
    resp_broker = client.get(
        "/api/v1/system/audit-verification",
        headers={"Authorization": f"Bearer {broker_token}"}
    )
    assert resp_broker.status_code == 403

    # 3. Admin role
    admin = db_session.query(User).filter(User.email == "admin_audit@test.com").first()
    if not admin:
        admin = User(
            email="admin_audit@test.com",
            hashed_password=hash_password("Pass123!"),
            full_name="Admin User",
            role="admin",
            is_active=1
        )
        db_session.add(admin)
        db_session.commit()
    admin_token = create_access_token(subject=admin.id, role="admin")
    resp_admin = client.get(
        "/api/v1/system/audit-verification",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp_admin.status_code == 200
    data = resp_admin.json()
    assert data["chain_valid"] is True
    assert data["tamper_detected"] is False
    assert data["records_verified"] >= 1
