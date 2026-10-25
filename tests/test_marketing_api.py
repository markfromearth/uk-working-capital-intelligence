"""
Tests for B2B Marketing Intelligence REST API and Frontend View Integration.
Validates channels lookup, strategy briefs, funnel economics calculations,
campaign tracking codes, CSV outcome batch imports, and HTML dashboard rendering.
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.core.security import create_access_token, hash_password
from backend.app.database import Base, engine, SessionLocal, init_db
from backend.app.main import app
from backend.app.models.user import User


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "marketing_api_broker@test.co.uk").first()
        if not user:
            user = User(
                email="marketing_api_broker@test.co.uk",
                hashed_password=hash_password("MarketingBrokerTest2026!"),
                full_name="Marketing API Test Broker",
                role="broker",
                is_active=1,
            )
            db.add(user)
            db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "marketing_api_broker@test.co.uk").first()
        token = create_access_token(subject=user.id, role=user.role)
    finally:
        db.close()
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def test_marketing_channels_api(client: TestClient):
    """Verify GET /api/v1/marketing/channels returns verified channels with unobserved reach flags."""
    res = client.get("/api/v1/marketing/channels?sector_keyword=transport")
    assert res.status_code == 200
    data = res.json()
    assert "count" in data
    assert "channels" in data
    assert data["count"] >= 1
    ch = data["channels"][0]
    assert "channel_name" in ch
    assert ch["reach_metric_text"] == "UNOBSERVED"
    assert ch["is_reach_verified"] is False


def test_marketing_strategies_generate_api(client: TestClient):
    """Verify POST /api/v1/marketing/strategies/generate/{crn} synthesizes differentiated briefs."""
    res = client.post(
        "/api/v1/marketing/strategies/generate/08931201",
        json={
            "facility_size_gbp": 450_000.0,
            "triggered_indicators": ["contract_award_growth", "white_space"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["crn"] == "08931201"
    assert data["strategies_count"] >= 1
    strat = data["strategies"][0]
    assert "strategy_id" in strat
    assert "executive_message" in strat
    assert "validation_test" in strat
    assert len(strat["compliance_guardrails"]) >= 1


def test_campaign_economics_planning_api(client: TestClient):
    """Verify POST /api/v1/marketing/campaigns/plan returns multi-scenario unit economics."""
    res = client.post(
        "/api/v1/marketing/campaigns/plan",
        json={
            "facility_size_gbp": 300_000.0,
            "campaign_cost_gbp": 750.0,
            "target_reach": 1200,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "scenarios" in data
    assert "CONSERVATIVE" in data["scenarios"]
    assert "BASE" in data["scenarios"]
    assert "OPTIMISTIC" in data["scenarios"]
    assert data["break_even_response_rate"] > 0.0
    assert data["max_recommended_test_budget_gbp"] > 0.0
    assert len(data["assumptions_log"]) >= 1


def test_campaign_tracking_and_outcomes_flow(client: TestClient):
    """Verify tracking code issuance, event recording, and empirical conversion metrics."""
    # 1. Issue tracking code
    track_res = client.post(
        "/api/v1/marketing/campaigns/track",
        json={
            "strategy_id": "STRAT-FLOW-01",
            "channel_name": "Commercial Logistics Digest",
            "channel_type": "PUBLICATION",
            "content_format": "TRADE_PUBLICATION_ARTICLE",
            "crn": "08931201",
            "budget_gbp": 0.0,
        },
    )
    assert track_res.status_code == 200
    track_data = track_res.json()
    cid = track_data["campaign_id"]
    assert cid.startswith("CMP-")

    # 2. List tracked campaigns
    list_res = client.get("/api/v1/marketing/campaigns?crn=08931201")
    assert list_res.status_code == 200
    assert list_res.json()["count"] >= 1

    # 3. Record outcome events via CSV
    csv_body = f"""campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp
{cid},IMPRESSION,08931201,0,0,2026-03-01T10:00:00Z
{cid},CLICK,08931201,0,0,2026-03-01T10:05:00Z
{cid},QUALIFIED_ENQUIRY,08931201,0,0,2026-03-02T11:00:00Z
{cid},APPLICATION,08931201,0,0,2026-03-03T14:00:00Z
{cid},FUNDED_DEAL,08931201,200000.0,3000.0,2026-03-05T15:00:00Z
"""
    csv_res = client.post(
        "/api/v1/marketing/campaigns/outcomes/csv",
        json={"csv_content": csv_body},
    )
    assert csv_res.status_code == 200
    assert csv_res.json()["counts"]["ingested"] == 5

    # 4. Check empirical metrics
    metrics_res = client.get(f"/api/v1/marketing/campaigns/metrics?campaign_id={cid}")
    assert metrics_res.status_code == 200
    m_data = metrics_res.json()
    assert m_data["impressions"] >= 1
    assert m_data["funded_deals"] >= 1
    assert m_data["total_funded_volume_gbp"] == 200_000.0
    assert m_data["total_realized_commission_gbp"] == 3_000.0


def test_frontend_renders_marketing_dashboard(client: TestClient):
    """Verify root GET / serves HTML index containing the Marketing Focus view and navigation."""
    res = client.get("/")
    assert res.status_code == 200
    content = res.text
    assert "id=\"view-marketing\"" in content
    assert "id=\"nav-btn-marketing\"" in content
    assert "Full-Funnel Economics & ROI Scenario Calculator" in content
    assert "B2B Marketing Intelligence & Commercial Activation" in content
