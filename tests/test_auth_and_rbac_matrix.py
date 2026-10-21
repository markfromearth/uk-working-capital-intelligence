import hashlib
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.core.security import hash_password, verify_password, create_access_token, decode_access_token, password_needs_rehash

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_login_incorrect_credentials(db_session):
    resp = client.post("/api/v1/auth/login", json={"email": "nonexistent@test.com", "password": "WrongPassword!"})
    assert resp.status_code == 401
    assert "Incorrect email or password" in resp.json()["detail"]

def test_legacy_password_verification_and_safe_upgrading(db_session):
    """
    Proves that a user stored with an older legacy password format (100,000 iterations salt:key)
    can authenticate successfully, and upon successful authentication, their password hash
    is automatically upgraded to the 210,000 iteration standard.
    """
    email = "legacy_user@test.co.uk"
    raw_pw = "LegacyPass2025!"
    
    # Craft legacy 100k hash: salt:key
    salt = "1234567890abcdef"
    legacy_key = hashlib.pbkdf2_hmac('sha256', raw_pw.encode('utf-8'), salt.encode('utf-8'), 100_000).hex()
    legacy_hash = f"{salt}:{legacy_key}"

    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=legacy_hash,
            full_name="Legacy User",
            role="broker",
            is_active=1
        )
        db_session.add(user)
    else:
        user.hashed_password = legacy_hash
    db_session.commit()
    db_session.refresh(user)

    assert password_needs_rehash(user.hashed_password) is True

    # Login with legacy password
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": raw_pw})
    assert resp.status_code == 200

    # Verify that user's password in database was upgraded
    db_session.refresh(user)
    assert user.hashed_password.startswith("pbkdf2_sha256$210000$")
    assert password_needs_rehash(user.hashed_password) is False

def test_expired_jwt_token_rejected(db_session):
    """Proves that a token expired in the past is rejected with HTTP 401."""
    expired_token = create_access_token(
        subject="test-user-id",
        role="broker",
        expires_delta=timedelta(minutes=-10)
    )
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401

def test_invalid_signature_and_unsupported_algorithm_rejected(db_session):
    """Proves that forged signatures or algorithm='none' attacks are rejected."""
    # 1. Signature forged with different key
    forged_token = jwt.encode(
        {"sub": "test-user-id", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "wrong-secret-key-32-bytes-long-!",
        algorithm="HS256"
    )
    resp1 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged_token}"})
    assert resp1.status_code == 401

    # 2. Algorithm 'none' attack
    none_token = jwt.encode(
        {"sub": "test-user-id", "role": "admin"},
        key="",
        algorithm="none"
    )
    resp2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {none_token}"})
    assert resp2.status_code == 401

def test_deactivated_user_token_rejected(db_session):
    """Proves that if an active user is deactivated, subsequent API calls are blocked with 401."""
    email = "deactivated_user@test.co.uk"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password("UserPass2026!"),
            full_name="Deactivated Employee",
            role="broker",
            is_active=1
        )
        db_session.add(user)
    else:
        user.is_active = 1
    db_session.commit()
    db_session.refresh(user)

    valid_token = create_access_token(subject=user.id, role="broker")
    
    # Verify active user succeeds
    resp1 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {valid_token}"})
    assert resp1.status_code == 200

    # Deactivate user
    user.is_active = 0
    db_session.commit()

    # Re-test with same token -> must be rejected
    resp2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {valid_token}"})
    assert resp2.status_code == 401

def test_rbac_administrative_endpoints_blocked_for_ordinary_broker(db_session):
    """
    Proves that an ordinary broker role cannot access:
      - DLQ Replay (403)
      - DLQ Inspection (403)
      - Demo Seed (403)
      - Audit Verification (403)
      - Entity Unmerge (403)
    """
    broker = db_session.query(User).filter(User.email == "broker_rbac_matrix@test.com").first()
    if not broker:
        broker = User(
            email="broker_rbac_matrix@test.com",
            hashed_password=hash_password("Pass123!"),
            full_name="Broker RBAC Matrix",
            role="broker",
            is_active=1
        )
        db_session.add(broker)
        db_session.commit()
    broker_token = create_access_token(subject=broker.id, role="broker")
    headers = {"Authorization": f"Bearer {broker_token}"}

    # 1. DLQ Replay
    assert client.post("/api/v1/ingest/dead-letter-queue/1/replay", headers=headers).status_code == 403

    # 2. DLQ List
    assert client.get("/api/v1/ingest/dead-letter-queue", headers=headers).status_code == 403

    # 3. Demo Seed
    assert client.post("/api/v1/system/seed-demo-data", headers=headers).status_code == 403

    # 4. Audit Verification
    assert client.get("/api/v1/system/audit-verification", headers=headers).status_code == 403

    # 5. Entity Unmerge
    assert client.post("/api/v1/reviews/unmerge", json={
        "source_identifier": "TEST-UNMERGE",
        "current_crn": "08931201",
        "reason": "Test unauthorized unmerge"
    }, headers=headers).status_code == 403
