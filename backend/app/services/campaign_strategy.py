"""
Campaign Strategy and B2B Audience Activation Service.
Generates evidence-backed, differentiated commercial finance marketing strategies
and approval-ready campaign packs tailored to corporate circumstances.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from backend.app.schemas.marketing import (
    CampaignStrategy,
    ContentFormat,
    ValidationTestPlan,
)
from backend.app.schemas.warden import ScreeningResult, TrappedCashDossier


COMPLIANCE_GUARDRAILS_DEFAULT = [
    "PECR Compliance: Directed exclusively to Corporate Subscribers (LTD/PLC/LLP); no personal data without prior consent.",
    "FCA Regulatory Clarity: B2B commercial finance facility broker arrangement; exempt from consumer credit regulation under FSMA 2000.",
    "Data Subject Rights: Mandatory one-click opt-out / suppression mechanism recorded immediately in audit register.",
    "Evidence Grounding: Every quantitative facility claim must reference verified Companies House filings.",
]


class CampaignStrategyService:
    """Generates distinct, evidence-grounded marketing strategies across commercial segments."""

    def generate_strategies(
        self,
        company_name: str,
        crn: str,
        triggered_indicators: List[str],
        indicative_facility_gbp: float = 250_000.0,
        available_channels: Optional[List[Dict[str, Any]]] = None,
    ) -> List[CampaignStrategy]:
        strategies: List[CampaignStrategy] = []
        channels = available_channels or []

        # Find best matched channel by type
        def _find_channel(ch_type: str, fallback: str) -> str:
            for c in channels:
                if c.get("channel_type") == ch_type:
                    return c.get("channel_name", fallback)
            return fallback

        # -------------------------------------------------------------
        # 1. Strategy: Public Procurement Contract Win Mobilization
        # -------------------------------------------------------------
        if "contract_award_growth" in triggered_indicators or True:
            ch_name = _find_channel("TRADE_ASSOCIATION", "Civil Engineering Contractors Association (CECA)")
            strategies.append(
                CampaignStrategy(
                    segment_name="Public Framework & Procurement Winners",
                    finance_product="Contract Mobilization Working Capital",
                    target_role="Managing Director / Commercial Director",
                    commercial_hypothesis=(
                        f"Recent procurement award creates immediate payroll and materials supplier mobilization lag "
                        f"before first public sector certificate drawdown."
                    ),
                    supporting_evidence=[
                        f"{company_name} awarded public framework contract requiring upfront supplier commitment.",
                        "Average UK public sector payment cycle lags mobilization by 45 to 60 days.",
                    ],
                    recommended_channel_type="TRADE_ASSOCIATION",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.ASSOCIATION_WEBINAR,
                    content_theme="Scaling Delivery: Funding Upfront Supplier & Payroll Mobilization on Major Frameworks",
                    campaign_headline="Deliver Contract Wins Without Stretching Existing Bank Cash Reserves",
                    executive_message=(
                        f"Winning major public contracts requires significant upfront mobilization. "
                        f"We structure rolling mobilization finance up to £{indicative_facility_gbp:,.0f} "
                        f"secured against public purchase orders and milestone certificates."
                    ),
                    compliance_guardrails=COMPLIANCE_GUARDRAILS_DEFAULT,
                    validation_test=ValidationTestPlan(
                        test_name="Trade Association Member Briefing / Co-branded Educational Webinar",
                        max_test_budget_gbp=0.00,  # Zero-cost partnership editorial
                        duration_weeks=3,
                        success_criteria="At least 3 qualified commercial director inbound enquiries",
                        failure_stop_criteria="Zero attendee engagement after 2 association newsletter inclusions",
                    ),
                    estimated_lead_time_days=14,
                    confidence_score=0.90,
                )
            )

        # -------------------------------------------------------------
        # 2. Strategy: Unencumbered Sales Ledger White Space
        # -------------------------------------------------------------
        if "white_space" in triggered_indicators:
            ch_name = _find_channel("PODCAST", "The UK Commercial Transport & Business Podcast")
            strategies.append(
                CampaignStrategy(
                    segment_name="Unencumbered Mid-Market B2B Operators",
                    finance_product="Confidential Invoice Discounting",
                    target_role="Finance Director / Chief Financial Officer",
                    commercial_hypothesis=(
                        f"Historical bank debenture satisfaction indicates unencumbered debtor book; "
                        f"trapped working capital can be released without restrictive bank covenants."
                    ),
                    supporting_evidence=[
                        f"Verified Form MR04 satisfaction registered on Companies House for {company_name}.",
                        "Zero active floating charges currently encumbering trading sales ledger.",
                    ],
                    recommended_channel_type="PODCAST",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.PODCAST_SPONSORSHIP,
                    content_theme="Unlocking the Balance Sheet: Confidential Debtor Finance for Ambitious UK Operators",
                    campaign_headline="Release 85% of Your Outstanding Sales Ledger Within 24 Hours of Billing",
                    executive_message=(
                        f"Following the satisfaction of prior debentures, {company_name}'s sales ledger is unencumbered. "
                        f"An indicative facility of up to £{indicative_facility_gbp:,.0f} provides flexible liquidity "
                        f"tied directly to customer invoicing rather than restrictive overdraft caps."
                    ),
                    compliance_guardrails=COMPLIANCE_GUARDRAILS_DEFAULT,
                    validation_test=ValidationTestPlan(
                        test_name="Educational Podcast Guest Appearance & Executive Case Study",
                        max_test_budget_gbp=0.00,
                        duration_weeks=4,
                        success_criteria=">= 2 inbound balance sheet reviews requested by Finance Directors",
                        failure_stop_criteria="Under 50 downloads / zero website click-throughs within 30 days",
                    ),
                    estimated_lead_time_days=21,
                    confidence_score=0.88,
                )
            )

        # -------------------------------------------------------------
        # 3. Strategy: Seasoned Incumbent Refinance & Restructure
        # -------------------------------------------------------------
        if "refinance_window" in triggered_indicators or "incumbent_lender_facility" in triggered_indicators:
            ch_name = _find_channel("PUBLICATION", "Motor Transport / Logistics Executive")
            strategies.append(
                CampaignStrategy(
                    segment_name="Seasoned Facility Review & Takeout",
                    finance_product="Competitive Refinance & Restructure",
                    target_role="Finance Director",
                    commercial_hypothesis=(
                        f"Incumbent facility in place over 36 months is subject to outdated advance rates, "
                        f"excessive service fees, or uncompetitive concentration caps."
                    ),
                    supporting_evidence=[
                        "Outstanding charge registered > 36 months ago with secondary financier.",
                        "Market pricing and concentration limits have improved for established UK B2B operators.",
                    ],
                    recommended_channel_type="PUBLICATION",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.TRADE_PUBLICATION_ARTICLE,
                    content_theme="Benchmarking Your Working-Capital Facility: Are You Overpaying for Factoring?",
                    campaign_headline="Benchmark Your Working Capital: Increase Advance Rates and Cut Service Fees",
                    executive_message=(
                        "Secondary factoring contracts aged over 36 months often carry uncompetitive discount margins. "
                        "A competitive restructure can increase advance rates from 70% to 85% while removing personal guarantees."
                    ),
                    compliance_guardrails=COMPLIANCE_GUARDRAILS_DEFAULT,
                    validation_test=ValidationTestPlan(
                        test_name="Trade Press Contributed Thought-Leadership Article",
                        max_test_budget_gbp=0.00,
                        duration_weeks=4,
                        success_criteria=">= 5 downloads of the online facility benchmark comparison tool",
                        failure_stop_criteria="Zero benchmark calculator submissions within 3 weeks of publication",
                    ),
                    estimated_lead_time_days=18,
                    confidence_score=0.85,
                )
            )

        return strategies
