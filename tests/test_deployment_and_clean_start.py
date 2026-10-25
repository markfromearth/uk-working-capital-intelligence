from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.core.security import create_access_token, hash_password
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.experian import ExperianAdapter
from backend.app.providers.workforce import WorkforceAdapter
from backend.app.providers.base import AdapterHealthStatus

client = TestClient(app)

def test_missing_credentials_does_not_silently_promote_fixtures_when_demo_mode_false():
    """
    Proves that when commercial credentials are absent and DEMO_MODE=False,
    the adapters truthfully return UNCONFIGURED status and empty data,
    PREVENTING silent substitution of simulated data for live data.
    """
    with patch.object(settings, "DEMO_MODE", False):
        # 1. Companies House unconfigured
        ch_adapter = CompaniesHouseAdapter(api_key=None)
        ch_adapter._is_live = False
        assert ch_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert ch_adapter.fetch_company_profile("08931201") is None
        assert ch_adapter.fetch_company_charges("08931201") == []

        # 2. Creditsafe unconfigured
        cs_adapter = CreditsafeAdapter(api_key=None)
        cs_adapter._is_live = False
        assert cs_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert cs_adapter.fetch_company_signals("08931201") == []

        # 3. Experian unconfigured
        exp_adapter = ExperianAdapter(api_key=None)
        exp_adapter._is_live = False
        assert exp_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert exp_adapter.fetch_company_signals("08931201") == []

        # 4. Workforce unconfigured
        wf_adapter = WorkforceAdapter(api_key=None)
        wf_adapter._is_live = False
        assert wf_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert wf_adapter.fetch_company_signals("08931201") == []

def test_clean_health_and_system_providers_endpoint():
    """Verifies that the /api/v1/system/health and /api/v1/system/providers endpoints respond accurately."""
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "deploy_test_broker@test.co.uk").first()
        if not user:
            user = User(
                email="deploy_test_broker@test.co.uk",
                hashed_password=hash_password("DeployBrokerPass2026!"),
                full_name="Deploy Test Broker",
                role="broker",
                is_active=1,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(subject=user.id, role=user.role)
    finally:
        db.close()

    health_resp = client.get("/api/v1/system/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] == "healthy"
    assert health_data["database_connected"] is True
    assert health_data["version"] == "1.0.0"

    prov_resp = client.get("/api/v1/system/providers", headers={"Authorization": f"Bearer {token}"})
    assert prov_resp.status_code == 200
    prov_data = prov_resp.json()
    assert len(prov_data) >= 4
    names = [p["provider_name"] for p in prov_data]
    assert "companies_house" in names
    assert "contracts_finder" in names
    assert "the_gazette" in names
    assert "creditsafe" in names
    assert "experian" in names
    assert "workforce_intelligence" in names
