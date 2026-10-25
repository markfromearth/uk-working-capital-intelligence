"""
Test Suite for Marketing Channel Discovery and Research Agent (Agent E).
Verifies zero-cost channel discovery across UK podcasts, trade associations,
publications, and conferences with strict prevention of fabricated metrics.
"""

import pytest
from backend.app.database import SessionLocal, init_db
from backend.app.models.marketing import (
    MarketingChannelCandidate,
    MarketingChannelType,
)
from backend.app.services.marketing_discovery import MarketingChannelDiscoveryService
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.agents.marketing_research import MarketingResearchAgent


@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


@pytest.mark.asyncio
async def test_podcast_discovery_enforces_unobserved_metric_rule(monkeypatch):
    import httpx
    from backend.app.config import settings
    from backend.app.core.ssrf_protection import SafeHttpClient

    async def _mock_itunes_get(self, url, params=None, headers=None, transport=None):
        return httpx.Response(
            200,
            json={
                "resultCount": 1,
                "results": [
                    {
                        "collectionName": "UK Commercial Fleet & Freight Weekly",
                        "artistName": "Freight Broadcast Network",
                        "collectionViewUrl": "https://podcasts.apple.com/gb/podcast/uk-commercial-fleet/id123456",
                        "feedUrl": "https://logistics.org.uk/podcast/feed.xml",
                        "trackCount": 142,
                    }
                ],
            },
        )

    monkeypatch.setattr(SafeHttpClient, "get", _mock_itunes_get)
    service = MarketingChannelDiscoveryService()
    podcasts = await service.search_podcasts("commercial fleet")
    assert len(podcasts) == 1

    for p in podcasts:
        assert p["channel_type"] == MarketingChannelType.PODCAST.value
        assert "channel_name" in p
        assert "website_uri" in p
        # Strict requirement: Never fabricate listener counts or ratings
        assert p["reach_metric_text"] == "UNOBSERVED"
        assert p["is_reach_verified"] is False
        assert p["provenance_mode"] == "LIVE"
        assert p["estimated_cost_tier"] in ("FREE_EDITORIAL", "UNKNOWN")

    # Verify timeout handling: DEMO_MODE=False returns [] (never fabricates positive/simulated channel)
    async def _mock_timeout_get(self, url, params=None, headers=None, transport=None):
        raise httpx.ReadTimeout("Simulated iTunes timeout")

    monkeypatch.setattr(SafeHttpClient, "get", _mock_timeout_get)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert await service.search_podcasts("commercial fleet") == []

    # In DEMO_MODE=True, fallback is explicitly labeled SIMULATED and UNOBSERVED
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    sim_podcasts = await service.search_podcasts("commercial fleet")
    assert len(sim_podcasts) == 1
    assert sim_podcasts[0]["provenance_mode"] == "SIMULATED"
    assert sim_podcasts[0]["verification_status"] == "simulated"
    assert sim_podcasts[0]["reach_metric_text"] == "UNOBSERVED"


def test_trade_associations_mapped_by_sic_prefix():
    service = MarketingChannelDiscoveryService()

    # Logistics / Transport (SIC 49)
    haulage_assocs = service.get_trade_associations(["49410"])
    assert len(haulage_assocs) >= 2
    names = [a["channel_name"] for a in haulage_assocs]
    assert "Road Haulage Association (RHA)" in names
    assert "Logistics UK" in names
    for a in haulage_assocs:
        assert a["channel_type"] == MarketingChannelType.TRADE_ASSOCIATION.value
        assert a["confidence_score"] >= 0.90
        assert a["website_uri"].startswith("https://")

    # Construction (SIC 41)
    const_assocs = service.get_trade_associations(["41201"])
    assert len(const_assocs) >= 1
    assert any("Build UK" in a["channel_name"] for a in const_assocs)

    # Manufacturing (SIC 25)
    mfg_assocs = service.get_trade_associations(["25620"])
    assert len(mfg_assocs) >= 1
    assert any("Make UK" in a["channel_name"] for a in mfg_assocs)


def test_trade_publications_and_conferences_mapped_by_sic():
    service = MarketingChannelDiscoveryService()

    pubs = service.get_trade_publications(["49410"])
    assert len(pubs) >= 1
    assert any("Motor Transport" in p["channel_name"] for p in pubs)

    events = service.get_conferences_and_events(["49410"])
    assert len(events) >= 1
    assert any("Commercial Vehicle Show" in e["channel_name"] for e in events)


@pytest.mark.asyncio
async def test_marketing_research_agent_execution_and_persistence(db_session):
    agent = MarketingResearchAgent()
    crn = "01989005"
    req = AgentTaskRequest(
        target_role=AgentRole.MARKETING_RESEARCH,
        crn=crn,
        budget_limit_gbp=0.00,
        input_payload={
            "sic_codes": ["49410"],
            "sector_keyword": "freight logistics",
        },
    )

    result = await agent.execute(req)
    assert result.status == AgentTaskStatus.COMPLETED
    assert result.role == AgentRole.MARKETING_RESEARCH
    assert result.cost_incurred_gbp == 0.00
    assert result.output_payload["total_channels_discovered"] >= 3
    assert result.output_payload["associations_count"] >= 1
    assert len(result.evidence_references) >= 3

    # Verify persisted records in database
    persisted = db_session.query(MarketingChannelCandidate).filter(
        MarketingChannelCandidate.channel_type == MarketingChannelType.TRADE_ASSOCIATION.value
    ).all()
    assert len(persisted) >= 1
    assert any("Road Haulage Association" in c.channel_name for c in persisted)
