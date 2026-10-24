"""
End-to-End Integration Tests for CH-Only Invoice Finance Warden Pipeline and REST API Surface.
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.schemas.warden import LendingArchetype
from backend.app.services.pipeline_orchestrator import (
    WardenPipelineOrchestrator,
    default_pipeline_orchestrator,
)
from backend.app.providers.companies_house_ch import CompaniesHouseCHClient


@pytest.fixture
def api_client():
    # Register hermetic qualified prospect fixture on global orchestrator
    default_pipeline_orchestrator.ch_client.register_fixture(
        "08421099",
        {
            "profile": {
                "company_number": "08421099",
                "company_name": "TITAN LOGISTICS LIMITED",
                "company_status": "active",
                "sic_codes": ["49410"],  # Freight transport by road (B2B prime)
                "accounts": {"overdue": False},
                "confirmation_statement": {"overdue": False},
            },
            "charges": [
                {
                    "charge_code": "001",
                    "status": "fully-satisfied",
                    "satisfied_on": "2026-04-10",
                    "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
                }
            ],
            "filing_history": [
                {
                    "category": "accounts",
                    "type": "AA",
                    "date": "2026-01-15",
                    "links": {"document_metadata": "https://document-api.company-information.service.gov.uk/document/doc123"},
                }
            ],
        },
    )
    sample_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2008/inlineXBRL" xmlns:uk-gaap="http://www.xbrl.org/uk/gaap/core/2009-09-01">
    <body>
        <ix:nonFraction name="uk-gaap:Debtors">450000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CurrentAssets">485000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CreditorsDueWithinOneYear">(260000)</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:NetCurrentAssetsLiabilities">225000</ix:nonFraction>
    </body>
    </html>""".encode("utf-8")
    default_pipeline_orchestrator.fetcher.register_fixture("doc123", sample_xhtml, "application/xhtml+xml")
    return TestClient(app)


@pytest.mark.asyncio
async def test_pipeline_orchestrator_qualified_flow():
    ch_client = CompaniesHouseCHClient()
    ch_client.register_fixture(
        "08421099",
        {
            "profile": {
                "company_number": "08421099",
                "company_name": "TITAN LOGISTICS LIMITED",
                "company_status": "active",
                "sic_codes": ["49410"],
                "accounts": {"overdue": False},
                "confirmation_statement": {"overdue": False},
            },
            "charges": [
                {
                    "charge_code": "001",
                    "status": "fully-satisfied",
                    "satisfied_on": "2026-04-10",
                    "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
                }
            ],
            "filing_history": [
                {
                    "category": "accounts",
                    "type": "AA",
                    "date": "2026-01-15",
                    "links": {"document_metadata": "https://document-api.company-information.service.gov.uk/document/doc123"},
                }
            ],
        },
    )
    fetcher = AccountsFetcher()
    sample_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2008/inlineXBRL" xmlns:uk-gaap="http://www.xbrl.org/uk/gaap/core/2009-09-01">
    <body>
        <ix:nonFraction name="uk-gaap:Debtors">450000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CurrentAssets">485000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CreditorsDueWithinOneYear">(260000)</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:NetCurrentAssetsLiabilities">225000</ix:nonFraction>
    </body>
    </html>""".encode("utf-8")
    fetcher.register_fixture("doc123", sample_xhtml, "application/xhtml+xml")

    orchestrator = WardenPipelineOrchestrator(ch_client=ch_client, fetcher=fetcher)
    result = await orchestrator.process_company(
        crn="08421099",
        min_gate_threshold=40.0,
    )

    assert result["status"] == "QUALIFIED"
    assert result["screening"] is not None
    assert result["screening"].passed_gate is True
    assert result["dossier"] is not None

    dossier = result["dossier"]
    assert dossier.indicative_facility_80pct > 0.0
    assert dossier.balance_sheet.is_arithmetic_valid is True
    assert "releases up to £" in dossier.commercial_pitch


@pytest.mark.asyncio
async def test_pipeline_orchestrator_below_gate_bypasses_stage2():
    ch_client = CompaniesHouseCHClient()
    # Register fixture for non-B2B company with no satisfied charges
    ch_client.register_fixture(
        "99999999",
        {
            "profile": {
                "company_number": "99999999",
                "company_name": "CORNER SWEET SHOP LTD",
                "company_status": "active",
                "sic_codes": ["47110"],  # Retail (Non-B2B)
                "accounts": {"overdue": False},
                "confirmation_statement": {"overdue": False},
            },
            "charges": [],
            "filing_history": [],
        },
    )

    orchestrator = WardenPipelineOrchestrator(ch_client=ch_client)
    result = await orchestrator.process_company(
        crn="99999999",
        min_gate_threshold=65.0,
    )

    assert result["status"] == "BELOW_GATE"
    assert result["screening"] is not None
    assert result["screening"].passed_gate is False
    assert result["dossier"] is None  # Stage 2 bypassed


def test_warden_api_endpoints(api_client):
    # 1. Test Health
    health_resp = api_client.get("/api/v1/warden/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["mode"] == "CH_ONLY_MVP"

    # 2. Test Screen Company
    screen_resp = api_client.post(
        "/api/v1/warden/screen/08421099",
        json={"min_gate_threshold": 40.0},
    )
    assert screen_resp.status_code == 200
    data = screen_resp.json()
    assert data["status"] == "QUALIFIED"
    assert data["dossier"] is not None

    # 3. Test Prospects List
    list_resp = api_client.get("/api/v1/warden/prospects")
    assert list_resp.status_code == 200
    prospects = list_resp.json()
    assert len(prospects) >= 1
    assert any(p["crn"] == "08421099" for p in prospects)

    # 4. Test Get Specific Dossier
    dossier_resp = api_client.get("/api/v1/warden/prospects/08421099/dossier")
    assert dossier_resp.status_code == 200
    dossier_data = dossier_resp.json()
    assert dossier_data["crn"] == "08421099"
    assert "commercial_pitch" in dossier_data
