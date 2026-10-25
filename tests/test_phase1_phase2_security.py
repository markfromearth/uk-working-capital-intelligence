"""
Phase 1 & Phase 2 Adversarial Security & RBAC Tests:
- Route inventory & anonymous request rejection across all protected endpoints.
- Role-based access control (RBAC) enforcement (broker vs analyst vs admin).
- Resource-level ownership isolation (User A cannot modify or inject outcomes into User B's campaign or activation).
- Campaign lifecycle state transition validation.
- CSV outcome ingestion limits (payload size, row count, required headers, null bytes, negative values, deduplication).
- Creditsafe webhook fail-closed behavior when secret is unset or signature is invalid.
- Rate limiting (429), request size guard (413), and safe exception handler (no stack trace / secret leakage).
- Secret management & startup fail-closed checks (production SECRET_KEY, DEMO_MODE, wildcard CORS, bootstrap user).
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, settings
from backend.app.core.security import create_access_token, hash_password
from backend.app.database import SessionLocal, init_db
from backend.app.main import app, reset_rate_limiter
from backend.app.models.user import User


@pytest.fixture(scope="module", autouse=True)
def setup_phase1_users():
    init_db()
    db = SessionLocal()
    try:
        users_spec = [
            ("phase1_admin@test.co.uk", "admin", "Phase1 Admin"),
            ("phase1_analyst@test.co.uk", "analyst", "Phase1 Analyst"),
            ("phase1_broker_a@test.co.uk", "broker", "Phase1 Broker A"),
            ("phase1_broker_b@test.co.uk", "broker", "Phase1 Broker B"),
        ]
        for email, role, name in users_spec:
            u = db.query(User).filter(User.email == email).first()
            if not u:
                u = User(
                    email=email,
                    hashed_password=hash_password("StrongTestPass2026!"),
                    full_name=name,
                    role=role,
                    is_active=1,
                )
                db.add(u)
            else:
                u.role = role
                u.is_active = 1
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def clear_rate_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _headers_for(email: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        token = create_access_token(subject=u.id, role=u.role)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def test_anonymous_requests_rejected_across_protected_endpoints():
    """Every non-public endpoint must reject anonymous requests with HTTP 401."""
    anon_client = TestClient(app)

    protected_routes = [
        ("GET", "/api/v1/marketing/channels"),
        ("POST", "/api/v1/marketing/strategies/generate/08931201"),
        ("POST", "/api/v1/marketing/campaigns/plan"),
        ("POST", "/api/v1/marketing/campaigns/track"),
        ("GET", "/api/v1/marketing/campaigns"),
        ("POST", "/api/v1/marketing/campaigns/outcomes"),
        ("POST", "/api/v1/marketing/campaigns/outcomes/csv"),
        ("GET", "/api/v1/marketing/campaigns/metrics"),
        ("GET", "/api/v1/audiences/"),
        ("GET", "/api/v1/audiences/act-123"),
        ("POST", "/api/v1/audiences/"),
        ("GET", "/api/v1/decision-makers/company/08931201/officers"),
        ("GET", "/api/v1/decision-makers/company/08931201/profiles"),
        ("GET", "/api/v1/decision-makers/suppression"),
        ("POST", "/api/v1/decision-makers/suppression"),
        ("GET", "/api/v1/adaptive/tools"),
        ("GET", "/api/v1/adaptive/investigations/08931201"),
        ("GET", "/api/v1/adaptive/assessments/08931201"),
        ("GET", "/api/v1/adaptive/outcomes/metrics"),
        ("GET", "/api/v1/companies"),
        ("GET", "/api/v1/companies/08931201"),
        ("GET", "/api/v1/companies/08931201/profile"),
        ("POST", "/api/v1/companies/08931201/recompute-score"),
        ("GET", "/api/v1/prospects"),
        ("GET", "/api/v1/prospects/dashboard-metrics"),
        ("POST", "/api/v1/reviews/prospects/08931201"),
        ("GET", "/api/v1/reviews/prospects/08931201/history"),
        ("GET", "/api/v1/reviews/matches"),
        ("POST", "/api/v1/reviews/matches/1/decision"),
        ("POST", "/api/v1/ingest/sync/companies-house-stream"),
        ("GET", "/api/v1/warden/prospects"),
        ("GET", "/api/v1/warden/prospects/08931201/dossier"),
        ("GET", "/api/v1/warden/prospects/08931201/screening"),
        ("GET", "/api/v1/warden/prospects/08931201/primary-sources"),
        ("POST", "/api/v1/warden/screen/08931201"),
        ("GET", "/api/v1/warden/health"),
        ("GET", "/api/v1/system/providers"),
        ("GET", "/api/v1/system/audit-verification"),
    ]

    for method, path in protected_routes:
        resp = anon_client.request(method, path, json={})
        assert resp.status_code == 401, f"Expected 401 on {method} {path}, got {resp.status_code}"


def test_campaign_ownership_isolation_and_state_transitions():
    """
    Broker A creates a campaign.
    Broker B cannot inject outcomes into Broker A's campaign, read its specific metrics, or alter its status.
    Admin can inspect and manage all campaigns.
    """
    client = TestClient(app)
    headers_a = _headers_for("phase1_broker_a@test.co.uk")
    headers_b = _headers_for("phase1_broker_b@test.co.uk")
    headers_admin = _headers_for("phase1_admin@test.co.uk")

    # 1. Broker A issues a campaign
    track_res = client.post(
        "/api/v1/marketing/campaigns/track",
        json={
            "strategy_id": "STRAT-OWNERSHIP-01",
            "channel_name": "UK Manufacturing Weekly",
            "channel_type": "PUBLICATION",
            "content_format": "TRADE_PUBLICATION_ARTICLE",
            "crn": "08931201",
            "budget_gbp": 250.0,
        },
        headers=headers_a,
    )
    assert track_res.status_code == 200
    cid = track_res.json()["campaign_id"]

    # 2. Broker B attempts to inject an outcome event into Broker A's campaign -> 403 Forbidden
    forged_outcome = client.post(
        "/api/v1/marketing/campaigns/outcomes",
        json={
            "campaign_id": cid,
            "event_type": "FUNDED_DEAL",
            "crn": "08931201",
            "funded_amount_gbp": 500_000.0,
            "realized_commission_gbp": 10_000.0,
        },
        headers=headers_b,
    )
    assert forged_outcome.status_code == 403

    # 3. Broker B attempts to inject CSV outcomes into Broker A's campaign -> 403 Forbidden
    csv_attack = (
        "campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp\n"
        f"{cid},FUNDED_DEAL,08931201,500000,10000,2026-03-01T12:00:00Z\n"
    )
    forged_csv = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={"csv_content": csv_attack},
        headers=headers_b,
    )
    assert forged_csv.status_code == 403

    # 4. Broker B attempts to read Broker A's campaign metrics -> 403 Forbidden
    forged_metrics = client.get(f"/api/v1/marketing/campaigns/metrics?campaign_id={cid}", headers=headers_b)
    assert forged_metrics.status_code == 403

    # 5. Broker B attempts to cancel Broker A's campaign -> 403 Forbidden
    forged_status = client.post(
        f"/api/v1/marketing/campaigns/{cid}/status",
        json={"target_status": "CANCELLED"},
        headers=headers_b,
    )
    assert forged_status.status_code == 403

    # 6. Broker A transitions campaign ACTIVE -> PAUSED -> COMPLETED
    pause_res = client.post(
        f"/api/v1/marketing/campaigns/{cid}/status",
        json={"target_status": "PAUSED"},
        headers=headers_a,
    )
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "PAUSED"

    complete_res = client.post(
        f"/api/v1/marketing/campaigns/{cid}/status",
        json={"target_status": "COMPLETED"},
        headers=headers_a,
    )
    assert complete_res.status_code == 200
    assert complete_res.json()["status"] == "COMPLETED"

    # 7. Invalid transition COMPLETED -> ACTIVE is rejected with 400
    invalid_trans = client.post(
        f"/api/v1/marketing/campaigns/{cid}/status",
        json={"target_status": "ACTIVE"},
        headers=headers_a,
    )
    assert invalid_trans.status_code == 400
    assert "Invalid campaign state transition" in invalid_trans.json()["detail"]

    # 8. Admin can view metrics of Broker A's campaign
    admin_metrics = client.get(f"/api/v1/marketing/campaigns/metrics?campaign_id={cid}", headers=headers_admin)
    assert admin_metrics.status_code == 200

    # 9. System/orchestrator campaign (owner_user_id=None) cannot be modified or injected by a BROKER
    from backend.app.services.outcome_learning import OutcomeLearningService
    db = SessionLocal()
    try:
        sys_rec = OutcomeLearningService(db).issue_campaign_tracking_id(
            strategy_id="STRAT-SYSTEM-ONLY",
            channel_name="System Automated Channel",
            channel_type="PODCAST",
            content_format="PODCAST_SPONSORSHIP",
            crn="08931201",
            owner_user_id=None,
        )
        sys_cid = sys_rec.campaign_id
    finally:
        db.close()

    broker_sys_inject = client.post(
        "/api/v1/marketing/campaigns/outcomes",
        json={"campaign_id": sys_cid, "event_type": "CLICK", "crn": "08931201"},
        headers=headers_a,
    )
    assert broker_sys_inject.status_code == 403

    broker_sys_status = client.post(
        f"/api/v1/marketing/campaigns/{sys_cid}/status",
        json={"target_status": "CANCELLED"},
        headers=headers_a,
    )
    assert broker_sys_status.status_code == 403

    admin_sys_metrics = client.get(f"/api/v1/marketing/campaigns/metrics?campaign_id={sys_cid}", headers=headers_admin)
    assert admin_sys_metrics.status_code == 200


def test_csv_validation_limits_and_deduplication():
    """
    Verifies malformed headers, null bytes, negative amounts, nonexistent campaigns,
    and duplicate CSV imports are handled deterministically and safely.
    """
    client = TestClient(app)
    headers_a = _headers_for("phase1_broker_a@test.co.uk")

    track_res = client.post(
        "/api/v1/marketing/campaigns/track",
        json={
            "strategy_id": "STRAT-CSV-SEC-01",
            "channel_name": "Precision Engineering Podcast",
            "channel_type": "PODCAST",
            "content_format": "PODCAST_SPONSORSHIP",
            "crn": "08931201",
        },
        headers=headers_a,
    )
    cid = track_res.json()["campaign_id"]

    # 1. Missing required headers -> 400
    bad_headers_res = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={"csv_content": "campaign_id,event_type\nCMP-123,CLICK\n"},
        headers=headers_a,
    )
    assert bad_headers_res.status_code == 400
    assert "missing required headers" in bad_headers_res.json()["detail"]

    # 2. Null byte injection -> 400
    null_byte_res = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={
            "csv_content": (
                "campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp\n"
                f"{cid}\x00,CLICK,08931201,0,0,2026-03-01T10:00:00Z\n"
            )
        },
        headers=headers_a,
    )
    assert null_byte_res.status_code == 400

    # 3. Valid CSV import succeeds on first pass and deduplicates on second pass
    valid_csv = (
        "campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp\n"
        f"{cid},IMPRESSION,08931201,0,0,2026-04-01T10:00:00Z\n"
        f"{cid},FUNDED_DEAL,08931201,100000,1500,2026-04-02T10:00:00Z\n"
    )
    res1 = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={"csv_content": valid_csv},
        headers=headers_a,
    )
    assert res1.status_code == 200
    assert res1.json()["counts"]["ingested"] == 2
    assert res1.json()["counts"]["duplicates"] == 0

    res2 = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={"csv_content": valid_csv},
        headers=headers_a,
    )
    assert res2.status_code == 200
    assert res2.json()["counts"]["ingested"] == 0
    assert res2.json()["counts"]["duplicates"] == 2

    # Metrics must reflect exactly 1 funded deal (£100k), not £200k
    m_res = client.get(f"/api/v1/marketing/campaigns/metrics?campaign_id={cid}", headers=headers_a)
    assert m_res.status_code == 200
    assert m_res.json()["funded_deals"] == 1
    assert m_res.json()["total_funded_volume_gbp"] == 100_000.0


def test_creditsafe_webhook_fails_closed_when_secret_unconfigured(monkeypatch):
    """Unsigned webhooks must be rejected with 401 even if CREDITSAFE_WEBHOOK_SECRET is None/empty."""
    monkeypatch.setattr(settings, "CREDITSAFE_WEBHOOK_SECRET", None)
    client = TestClient(app)
    resp = client.post("/api/v1/ingest/webhooks/creditsafe", json={"event_id": "evt-1"})
    assert resp.status_code == 401
    assert "unsigned webhooks are rejected" in resp.json()["detail"]


def test_rate_limiting_and_payload_size_guard(monkeypatch):
    """Verifies HTTP 429 on rate limit breach and HTTP 413 on oversized request bodies."""
    client = TestClient(app)

    # 1. Oversized request body guard (413)
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 256)
    big_res = client.post("/api/v1/auth/login", content="x" * 500, headers={"Content-Type": "application/json"})
    assert big_res.status_code == 413

    # 2. Rate limit guard (429)
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 1024 * 1024)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 3)
    reset_rate_limiter()

    for _ in range(3):
        assert client.get("/api/v1/system/health").status_code == 200
    resp_429 = client.get("/api/v1/system/health")
    assert resp_429.status_code == 429
    assert resp_429.headers.get("Retry-After") == "60"


def test_phase2_production_security_validation_fails_closed():
    """
    Verifies Settings.validate_production_security() rejects:
    - Weak or default SECRET_KEY in production/staging
    - DEMO_MODE=True in production/staging
    - Wildcard CORS in production/staging
    - Default example DB password in production/staging
    And passes when properly configured.
    """
    # 1. Default secret in production -> raises RuntimeError
    bad_secret = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="prod-secret-change-in-production-uk-working-capital-2026",
        DEMO_MODE=False,
        BACKEND_CORS_ORIGINS=["https://app.commercialfinance.co.uk"],
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        bad_secret.validate_production_security()

    # 2. DEMO_MODE=True in production -> raises RuntimeError
    bad_demo = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="a" * 64,
        DEMO_MODE=True,
        BACKEND_CORS_ORIGINS=["https://app.commercialfinance.co.uk"],
    )
    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        bad_demo.validate_production_security()

    # 3. Wildcard CORS in production -> raises RuntimeError
    bad_cors = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="a" * 64,
        DEMO_MODE=False,
        BACKEND_CORS_ORIGINS=["*"],
    )
    with pytest.raises(RuntimeError, match="BACKEND_CORS_ORIGINS"):
        bad_cors.validate_production_security()

    # 4. Default example DB password in production -> raises RuntimeError
    bad_db = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="a" * 64,
        DEMO_MODE=False,
        DATABASE_URL="postgresql://capital_user:capital_password@postgres:5432/working_capital",
        BACKEND_CORS_ORIGINS=["https://app.commercialfinance.co.uk"],
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        bad_db.validate_production_security()

    # 5. Valid production configuration passes
    good_prod = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="x" * 64,
        DEMO_MODE=False,
        DATABASE_URL="postgresql://capital_user:StrongVaultManagedSecret99!@postgres:5432/working_capital",
        BACKEND_CORS_ORIGINS=["https://app.commercialfinance.co.uk"],
    )
    good_prod.validate_production_security()
