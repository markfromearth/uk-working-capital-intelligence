"""
Agent A: Signal Detection Agent.
Detects potential commercial events and statutory changes from verified public providers.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.providers.registry import provider_registry


class SignalDetectionAgent(BaseAgent):
    """Detects commercial events across Companies House, Contracts Finder, and The Gazette."""

    def __init__(self):
        super().__init__(AgentRole.SIGNAL_DETECTION)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        crn = request.crn
        signals: List[Dict[str, Any]] = []
        evidence_refs: List[Dict[str, Any]] = []
        total_cost = 0.00  # Free public data sources

        # 1. Companies House Public Register
        ch_adapter = provider_registry.get_adapter("companies_house")
        if ch_adapter:
            try:
                ch_sigs = ch_adapter.fetch_company_signals(crn)
                signals.extend(ch_sigs)
                for s in ch_sigs:
                    if s.get("evidence_reference"):
                        evidence_refs.append({
                            "source": "COMPANIES_HOUSE",
                            "uri": s["evidence_reference"],
                            "ref": s.get("original_event_ref"),
                        })
            except Exception:
                pass

        # 2. Contracts Finder OCDS Awards
        cf_adapter = provider_registry.get_adapter("contracts_finder")
        if cf_adapter:
            try:
                cf_sigs = cf_adapter.fetch_company_signals(crn)
                signals.extend(cf_sigs)
                for s in cf_sigs:
                    if s.get("evidence_reference"):
                        evidence_refs.append({
                            "source": "CONTRACTS_FINDER",
                            "uri": s["evidence_reference"],
                            "ref": s.get("original_event_ref"),
                        })
            except Exception:
                pass

        # 3. The Gazette Statutory Insolvency Notices
        gz_adapter = provider_registry.get_adapter("the_gazette")
        if gz_adapter:
            try:
                gz_sigs = gz_adapter.fetch_company_signals(crn)
                signals.extend(gz_sigs)
                for s in gz_sigs:
                    if s.get("evidence_reference"):
                        evidence_refs.append({
                            "source": "THE_GAZETTE",
                            "uri": s["evidence_reference"],
                            "ref": s.get("original_event_ref"),
                        })
            except Exception:
                pass

        output_payload = {
            "signals_detected_count": len(signals),
            "signals": signals,
            "has_growth_signals": any(s.get("signal_type") == "contract_award_growth" for s in signals),
            "has_distress_signals": any(s.get("signal_type") == "gazette_insolvency_notice" for s in signals),
        }

        return output_payload, evidence_refs, total_cost
