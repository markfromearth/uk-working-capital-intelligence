import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.core.security import create_access_token, hash_password
from backend.app.services.seed_data import seed_demonstration_data

@pytest.fixture(scope="module", autouse=True)
def setup_api_test():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)
    user = db.query(User).filter(User.email == "api_review_broker@test.co.uk").first()
    if not user:
        user = User(
            email="api_review_broker@test.co.uk",
            hashed_password=hash_password("BrokerReviewPass2026!"),
            full_name="API Review Test Broker",
            role="broker",
            is_active=1,
        )
        db.add(user)
        db.commit()
    db.close()

def _auth_headers():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "api_review_broker@test.co.uk").first()
        token = create_access_token(subject=user.id, role=user.role)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()

client = TestClient(app)

def test_serve_frontend_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "UK WORKING-CAPITAL INTELLIGENCE" in response.text
    assert "Prioritised Prospects" in response.text

def test_system_providers():
    response = client.get("/api/v1/system/providers", headers=_auth_headers())
    assert response.status_code == 200
    providers = response.json()
    assert len(providers) >= 4
    names = [p["provider_name"] for p in providers]
    assert "companies_house" in names
    assert "creditsafe" in names
    assert "workforce_intelligence" in names

def test_dashboard_metrics():
    response = client.get("/api/v1/prospects/dashboard-metrics", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "total_monitored_companies" in data
    assert "band_distribution" in data
    assert "provider_integrations" in data

def test_prospect_table_filtering():
    # Filter by band
    response = client.get("/api/v1/prospects?score_band=strong", headers=_auth_headers())
    assert response.status_code == 200
    prospects = response.json()
    for p in prospects:
        assert p["score_band"] == "strong"
        assert p["liquidity_strain_score"] >= 75.0

def test_company_profile_endpoint():
    # Using seeded Apex Precision Engineering Ltd (CRN 08931201)
    response = client.get("/api/v1/companies/08931201/profile", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["company"]["crn"] == "08931201"
    assert "score_summary" in data
    assert "feature_snapshot" in data
    assert "explanation" in data
    assert "signals_timeline" in data
    assert data["feature_snapshot"]["dso_data_available"] is False
    assert "No genuine receivables ledger" in data["feature_snapshot"]["dso_unavailable_reason"]

def test_broker_review_workflow():
    crn = "08931201"
    review_payload = {
        "review_status": "in_review",
        "assigned_broker_name": "Senior Broker Jane Doe",
        "notes": "Reviewed MR04 debenture release and elevated DBT; requesting verification of current lender headroom."
    }
    response = client.post(f"/api/v1/reviews/prospects/{crn}", json=review_payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "in_review"
    assert data["assigned_broker_name"] == "Senior Broker Jane Doe"

    # Verify history
    hist_resp = client.get(f"/api/v1/reviews/prospects/{crn}/history", headers=_auth_headers())
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) >= 1
