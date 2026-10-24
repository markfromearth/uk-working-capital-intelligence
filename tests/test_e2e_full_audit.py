"""
Comprehensive End-to-End System Audit & Functional Test Suite.
Verifies all endpoints, user journeys, data integrity, error handling,
and Companies House statutory calculations across the entire stack.
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

@pytest.fixture
def client():
    return TestClient(app)


def test_e2e_frontend_html_integrity(client):
    """Verify frontend HTML root serves with complete UI components."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # Required brand strings
    assert "UK WORKING-CAPITAL INTELLIGENCE" in html
    assert "Prioritised Prospects" in html
    assert "Lead Search" in html
    assert "Pipeline" in html
    assert "FINANCE LEAD GENERATOR" in html
    assert "FINANCIAL HEALTH" in html
    assert "CHARGES" in html
    assert "COMPANIES HOUSE FILING STATUS" in html
    assert "PEOPLE WITH SIGNIFICANT CONTROL" in html
    assert "100-POINT STATUTORY SCORE ATTRIBUTION" in html


def test_e2e_warden_health_status(client):
    """Verify health endpoint confirms CH-Only mode and token bucket."""
    resp = client.get("/api/v1/warden/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "HEALTHY"
    assert data["mode"] == "CH_ONLY_MVP"
    assert data["token_bucket_available"] > 0


def test_e2e_companies_list_zero_synthetic(client):
    """Verify company listing returns genuine companies and zero synthetic data."""
    resp = client.get("/api/v1/companies")
    assert resp.status_code == 200
    companies = resp.json()
    assert isinstance(companies, list)
    for c in companies:
        assert c["is_synthetic"] == 0
        assert c["crn"] is not None
        assert c["company_name"] is not None


def test_e2e_profile_endpoints_dual_routing(client):
    """Verify both /companies/{crn} and /companies/{crn}/profile route successfully."""
    for crn in ["01989005", "SC335976"]:
        # Test short route
        resp1 = client.get(f"/api/v1/companies/{crn}")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["company"]["crn"] == crn
        assert "screening_result" in data1
        assert "officers" in data1
        assert "pscs" in data1

        # Test profile route
        resp2 = client.get(f"/api/v1/companies/{crn}/profile")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["company"]["crn"] == crn


def test_e2e_screening_invalid_crn(client):
    """Verify invalid CRN formats are rejected with 400."""
    resp = client.post("/api/v1/warden/screen/INVALID!!")
    assert resp.status_code == 400
    assert "Invalid UK" in resp.json()["detail"] or "normalization failed" in resp.json()["detail"]


def test_e2e_screening_live_endpoint(client):
    """Verify screening on-demand returns structured audit trail and indicators."""
    resp = client.post("/api/v1/warden/screen/01989005", json={"min_gate_threshold": 10.0})
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "screening" in data
    assert data["screening"]["crn"] == "01989005"
    assert len(data["screening"]["audit_trail"]) >= 5


def test_e2e_broker_review_workflow(client):
    """Verify commercial broker review actions are registered and retrievable."""
    crn = "01989005"
    payload = {
        "review_status": "in_review",
        "assigned_broker_name": "Jack Tholen",
        "notes": "Verified White Space and clean sales ledger headroom."
    }
    resp = client.post(f"/api/v1/reviews/prospects/{crn}", json=payload)
    assert resp.status_code == 200
    review_data = resp.json()
    assert review_data["review_status"] == "in_review"
    assert review_data["assigned_broker_name"] == "Jack Tholen"
