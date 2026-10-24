"""
Agent E: Marketing Research Agent.
Researches suitable B2B podcasts, trade associations, conferences, and publications
for identified commercial opportunities without fabricating audience statistics.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.database import SessionLocal
from backend.app.services.marketing_discovery import MarketingChannelDiscoveryService


class MarketingResearchAgent(BaseAgent):
    """Discovers compliant, verified marketing channels tailored to company B2B sector."""

    def __init__(self):
        super().__init__(AgentRole.MARKETING_RESEARCH)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        db = SessionLocal()
        evidence_refs: List[Dict[str, Any]] = []
        try:
            discovery = MarketingChannelDiscoveryService(db)
            sic_codes = request.input_payload.get("sic_codes", ["49410"])
            keyword = request.input_payload.get("sector_keyword", "logistics and transport")

            discovery_res = await discovery.discover_channels_for_company(
                crn=request.crn,
                sic_codes=sic_codes,
                sector_keyword=keyword,
            )

            for ch in discovery_res.get("channels", []):
                evidence_refs.append({
                    "source": ch.get("channel_type", "MARKETING_CHANNEL"),
                    "uri": ch.get("website_uri", "https://example.com"),
                    "ref": ch.get("channel_name"),
                })

            output_payload = {
                "crn": request.crn,
                "total_channels_discovered": discovery_res["total_channels_discovered"],
                "podcasts_count": discovery_res["podcasts_count"],
                "associations_count": discovery_res["associations_count"],
                "publications_count": discovery_res["publications_count"],
                "conferences_count": discovery_res["conferences_count"],
                "channel_candidates": discovery_res["channels"],
            }

            return output_payload, evidence_refs, 0.00
        finally:
            db.close()
