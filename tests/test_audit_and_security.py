import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import AuditLog
from backend.app.services.audit import log_audit_event, verify_audit_log_chain
from backend.app.core.security import hash_password, verify_password

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_tamper_evident_audit_log_hash_chain(db_session):
    # Clean audit logs for test isolation
    db_session.query(AuditLog).delete()
    db_session.commit()

    # Log two distinct events
    entry1 = log_audit_event(
        db=db_session,
        action="test_action_alpha",
        resource_type="company",
        resource_id="08931201",
        user_email="broker@test.co.uk",
        details={"notes": "First verified audit step"}
    )
    assert entry1.record_hash is not None
    assert entry1.prev_hash is not None

    entry2 = log_audit_event(
        db=db_session,
        action="test_action_beta",
        resource_type="company",
        resource_id="08931201",
        user_email="analyst@test.co.uk",
        details={"notes": "Second verified audit step"}
    )
    assert entry2.prev_hash == entry1.record_hash

    # Verify chain integrity
    is_valid, count, err = verify_audit_log_chain(db_session)
    assert is_valid is True
    assert count >= 2
    assert err is None

def test_audit_log_tamper_detection(db_session):
    # Retrieve the last audit entry and intentionally tamper with its details
    last_entry = db_session.query(AuditLog).order_by(AuditLog.id.desc()).first()
    original_details = last_entry.details_json
    
    try:
        # Malicious modification in place
        last_entry.details_json = '{"tampered": true}'
        db_session.commit()

        # Chain verification must catch the discrepancy
        is_valid, record_id, err = verify_audit_log_chain(db_session)
        assert is_valid is False
        assert "Tamper detected" in err
    finally:
        # Restore original state
        last_entry.details_json = original_details
        db_session.commit()

def test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility():
    pw = "FintechBrokerSecure2026!"
    
    # 1. Modern hash (210,000 iterations)
    modern_hash = hash_password(pw)
    assert modern_hash.startswith("pbkdf2_sha256$210000$")
    assert verify_password(pw, modern_hash) is True
    assert verify_password("WrongPassword", modern_hash) is False

    # 2. Legacy hash (100,000 iterations format)
    import hashlib
    salt = "abcd1234ef567890"
    key = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt.encode('utf-8'), 100_000).hex()
    legacy_hash = f"{salt}:{key}"
    assert verify_password(pw, legacy_hash) is True
    assert verify_password("WrongPassword", legacy_hash) is False

def test_security_headers_on_responses():
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    headers = response.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
