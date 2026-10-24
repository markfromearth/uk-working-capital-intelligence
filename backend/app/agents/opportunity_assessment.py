"""
Agent D: Finance Opportunity Agent.
Assesses potential financing need, product suitability, borrowing base, and confidence.
"""

from typing import Any, Dict, List, Tuple
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.database import SessionLocal
from backend.app.services.opportunity_assessment import CommercialOpportunityAssessor
from backend.app.services.product_matcher import match_products
from backend.app.schemas.warden import ScreeningResult, IndicatorBreakdown, PrimarySourceReference


class FinanceOpportunityAgent(BaseAgent):
    """Calculates P(Opportunity), P(Funded), facility sizing, and product shelf fit."""

    def __init__(self):
        super().__init__(AgentRole.FINANCE_OPPORTUNITY)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        db = SessionLocal()
        evidence_refs: List[Dict[str, Any]] = []
        try:
            assessor = CommercialOpportunityAssessor(db)
            assessment = assessor.assess_company(request.crn)

            # Construct mock ScreeningResult for product matching if Stage 1 signals exist
            signals = request.input_payload.get("signals", [])
            indicators: List[IndicatorBreakdown] = []

            for s in signals:
                stype = s.get("signal_type")
                if stype == "contract_award_growth":
                    indicators.append(
                        IndicatorBreakdown(
                            indicator="contract_award_growth",
                            weight=10.0,
                            awarded=10.0,
                            triggered=True,
                            evidence=s.get("value_text", "Public contract win"),
                        )
                    )
                if s.get("evidence_reference"):
                    evidence_refs.append({
                        "source": s.get("source_provider", "STATUTORY"),
                        "uri": s["evidence_reference"],
                        "ref": s.get("original_event_ref"),
                    })

            screening = ScreeningResult(
                crn=request.crn,
                company_name=request.input_payload.get("company_name", f"Company {request.crn}"),
                sic_codes=["49410"],
                is_b2b_eligible=True,
                provisional_score=75.0 if indicators else 40.0,
                passed_gate=True,
                knockout_applied=False,
                audit_trail=indicators,
            )
            matched = match_products(screening)

            output_payload = {
                "crn": request.crn,
                "p_qualified_opportunity": assessment.p_qualified_opportunity,
                "p_conversion_funded": assessment.p_conversion_funded,
                "estimated_facility_size_gbp": assessment.estimated_facility_size_gbp,
                "estimated_contribution_gbp": assessment.estimated_commercial_contribution_gbp,
                "recommended_action": assessment.recommended_action,
                "matched_products": [p["code"] for p in matched],
            }

            return output_payload, evidence_refs, 0.00
        finally:
            db.close()
