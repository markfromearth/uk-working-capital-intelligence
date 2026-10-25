"""
Campaign Strategy and B2B Audience Activation Service.
Generates evidence-backed, differentiated commercial finance marketing strategies
and approval-ready campaign packs tailored to corporate circumstances.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from backend.app.schemas.marketing import (
    CampaignStrategy,
    ConfidenceContract,
    ContentFormat,
    ValidationTestPlan,
)
from backend.app.schemas.warden import ScreeningResult, TrappedCashDossier


COMPLIANCE_GUARDRAILS_DEFAULT = [
    "PECR Compliance: Directed exclusively to verified UK Corporate Subscribers (LTD/PLC/LLP/Scottish Partnership) under Reg 22; Reg 23 sender identity and valid opt-out address mandatory.",
    "FCA Regulatory Clarity: B2B commercial finance facility broker arrangement; exempt from consumer credit regulation under FSMA 2000 where borrower is a body corporate.",
    "Data Subject Rights: Mandatory one-click opt-out / suppression mechanism recorded immediately in immutable suppression register.",
    "Evidence Grounding: Every quantitative facility or filing claim must reference verified primary source records and account for Companies House 21-day charge registration lag (CA 2006 s.859A).",
]


class CampaignStrategyService:
    """Generates distinct, evidence-grounded marketing strategies across commercial segments."""

    @staticmethod
    def _build_confidence_contract(
        source_reliability: float,
        entity_resolution_confidence: float,
        evidence_completeness: float,
        hypothesis_confidence: float,
        verification_status: str = "VERIFIED",
        data_freshness_status: str = "CURRENT",
        calibration_status: str = "UNCALIBRATED_PRIOR",
    ) -> ConfidenceContract:
        penalty = 1.0
        v_norm = (verification_status or "VERIFIED").upper()
        if v_norm in ("SIMULATED", "UNVERIFIED"):
            penalty = 0.55
        elif v_norm in ("FIXTURE", "PARTIALLY_VERIFIED"):
            penalty = 0.80

        if (data_freshness_status or "CURRENT").upper() == "STALE":
            penalty *= 0.85

        raw_composite = (
            0.30 * source_reliability
            + 0.25 * entity_resolution_confidence
            + 0.25 * evidence_completeness
            + 0.20 * hypothesis_confidence
        ) * penalty
        composite = round(max(0.0, min(1.0, raw_composite)), 4)
        return ConfidenceContract(
            source_reliability=round(source_reliability, 4),
            entity_resolution_confidence=round(entity_resolution_confidence, 4),
            evidence_completeness=round(evidence_completeness, 4),
            hypothesis_confidence=round(hypothesis_confidence, 4),
            calibration_status=calibration_status,
            data_freshness_status=data_freshness_status,
            verification_status=v_norm,
            composite_confidence=composite,
        )

    def generate_strategies(
        self,
        company_name: str,
        crn: str,
        triggered_indicators: List[str],
        indicative_facility_gbp: float = 250_000.0,
        available_channels: Optional[List[Dict[str, Any]]] = None,
        has_active_charges: bool = False,
        active_charge_count: int = 0,
        verification_status: str = "VERIFIED",
        data_freshness_status: str = "CURRENT",
        entity_resolution_confidence: float = 0.90,
        evidence_ids: Optional[List[str]] = None,
    ) -> List[CampaignStrategy]:
        strategies: List[CampaignStrategy] = []
        channels = available_channels or []
        clean_indicators = [i.strip() for i in (triggered_indicators or []) if i and i.strip()]
        if not clean_indicators:
            return []

        ev_ids = list(evidence_ids or [f"EV-{crn}-{ind.upper()}" for ind in clean_indicators])

        # Find best matched channel by type
        def _find_channel(ch_type: str, fallback: str) -> str:
            for c in channels:
                if c.get("channel_type") == ch_type:
                    return c.get("channel_name", fallback)
            return fallback

        # -------------------------------------------------------------
        # 1. Strategy: Public Procurement Contract Win Mobilization
        #    ONLY generated when 'contract_award_growth' is explicitly in triggered_indicators
        # -------------------------------------------------------------
        if "contract_award_growth" in clean_indicators:
            ch_name = _find_channel("TRADE_ASSOCIATION", "Civil Engineering Contractors Association (CECA)")
            conf = self._build_confidence_contract(
                source_reliability=0.92,
                entity_resolution_confidence=entity_resolution_confidence,
                evidence_completeness=0.85,
                hypothesis_confidence=0.80,
                verification_status=verification_status,
                data_freshness_status=data_freshness_status,
            )
            strategies.append(
                CampaignStrategy(
                    segment_name="Public Framework & Procurement Winners",
                    finance_product="Contract Mobilization Working Capital",
                    target_role="Managing Director / Commercial Director",
                    commercial_hypothesis=(
                        "Recent procurement award creates potential upfront payroll and materials supplier mobilization "
                        "working-capital lag prior to first public sector milestone certificate drawdown."
                    ),
                    supporting_evidence=[
                        f"{company_name} (CRN {crn}) matched to published public procurement contract award notice.",
                        "Average UK public sector milestone certification and payment cycle spans 30 to 60 days from mobilization.",
                    ],
                    observed_facts=[
                        f"Published procurement award notice linked to {company_name} (CRN {crn}).",
                    ],
                    derived_features=[
                        "indicator:contract_award_growth",
                        f"indicative_mobilization_facility_gbp:{indicative_facility_gbp:.2f}",
                    ],
                    invalidation_criteria=[
                        "Award notice is a non-binding framework lot with £0 guaranteed call-off volume.",
                        "Contract includes upfront mobilization advance payment from the contracting authority.",
                        "Entity match between procurement supplier name and CRN is disproved on manual review.",
                    ],
                    evidence_ids=[eid for eid in ev_ids if "CONTRACT" in eid or eid.startswith("EV-")],
                    confidence_contract=conf,
                    recommended_channel_type="TRADE_ASSOCIATION",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.ASSOCIATION_WEBINAR,
                    content_theme="Scaling Delivery: Funding Upfront Supplier & Payroll Mobilization on Major Frameworks",
                    campaign_headline="Deliver Contract Wins Without Stretching Existing Bank Cash Reserves",
                    executive_message=(
                        f"Winning major public contracts can require upfront mobilization expenditure before milestone certification. "
                        f"We structure rolling mobilization finance up to £{indicative_facility_gbp:,.0f} "
                        f"subject to contract terms and credit underwriting."
                    ),
                    compliance_guardrails=COMPLIANCE_GUARDRAILS_DEFAULT,
                    validation_test=ValidationTestPlan(
                        test_name="Trade Association Member Briefing / Co-branded Educational Webinar",
                        max_test_budget_gbp=0.00,
                        duration_weeks=3,
                        success_criteria="At least 3 qualified commercial director inbound enquiries",
                        failure_stop_criteria="Zero attendee engagement after 2 association newsletter inclusions",
                    ),
                    estimated_lead_time_days=14,
                    confidence_score=conf.composite_confidence,
                )
            )

        # -------------------------------------------------------------
        # 2. Strategy: Unencumbered Sales Ledger White Space
        #    ONLY generated when 'white_space' is in triggered_indicators AND no conflicting active charge blocks it
        # -------------------------------------------------------------
        if "white_space" in clean_indicators and not has_active_charges and active_charge_count == 0:
            ch_name = _find_channel("PODCAST", "The UK Commercial Transport & Business Podcast")
            conf = self._build_confidence_contract(
                source_reliability=0.95,
                entity_resolution_confidence=1.0,
                evidence_completeness=0.82,
                hypothesis_confidence=0.78,
                verification_status=verification_status,
                data_freshness_status=data_freshness_status,
            )
            strategies.append(
                CampaignStrategy(
                    segment_name="Unencumbered Mid-Market B2B Operators",
                    finance_product="Confidential Invoice Discounting",
                    target_role="Finance Director / Chief Financial Officer",
                    commercial_hypothesis=(
                        "Historical Form MR04 charge satisfaction with zero currently observed active charges on the "
                        "Companies House register suggests a potentially unencumbered debtor book, subject to verifying "
                        "the full charge register and accounting for the 21-day statutory filing period (CA 2006 s.859A)."
                    ),
                    supporting_evidence=[
                        f"Verified Form MR04 satisfaction filing recorded on Companies House for {company_name} (CRN {crn}).",
                        "No active floating charges observed on the Companies House mortgage register as of latest snapshot "
                        "(subject to the statutory 21-day registration window under Companies Act 2006 s.859A).",
                    ],
                    observed_facts=[
                        f"Companies House filing history for CRN {crn} contains a registered Form MR04 charge satisfaction.",
                        f"Observed active charge count on register at snapshot time: {active_charge_count}.",
                    ],
                    derived_features=[
                        "indicator:white_space",
                        "active_floating_charges_observed:0",
                    ],
                    invalidation_criteria=[
                        "Another unsatisfied fixed or floating charge exists on the complete mortgage register.",
                        "A new debenture was executed within the preceding 21 days and is pending Companies House registration.",
                        "Target company trades on cash-on-delivery or consumer terms without B2B trade debtor invoices.",
                    ],
                    evidence_ids=[eid for eid in ev_ids if "WHITE" in eid or eid.startswith("EV-")],
                    confidence_contract=conf,
                    recommended_channel_type="PODCAST",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.PODCAST_SPONSORSHIP,
                    content_theme="Unlocking the Balance Sheet: Confidential Debtor Finance for Ambitious UK Operators",
                    campaign_headline="Release Up to 85% of Eligible Outstanding Sales Ledger Invoices Within 24 Hours of Billing",
                    executive_message=(
                        f"Following the registered satisfaction of prior charges (Form MR04), {company_name}'s trade debtor ledger "
                        f"may be eligible for an unencumbered working-capital facility. "
                        f"An indicative facility of up to £{indicative_facility_gbp:,.0f} (subject to full charge register verification "
                        f"and ledger audit) provides flexible liquidity tied to customer invoicing."
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
                    confidence_score=conf.composite_confidence,
                )
            )

        # -------------------------------------------------------------
        # 3. Strategy: Seasoned Incumbent Refinance & Restructure
        # -------------------------------------------------------------
        if "refinance_window" in clean_indicators or "incumbent_lender_facility" in clean_indicators:
            ch_name = _find_channel("PUBLICATION", "Motor Transport / Logistics Executive")
            conf = self._build_confidence_contract(
                source_reliability=0.90,
                entity_resolution_confidence=1.0,
                evidence_completeness=0.80,
                hypothesis_confidence=0.75,
                verification_status=verification_status,
                data_freshness_status=data_freshness_status,
            )
            strategies.append(
                CampaignStrategy(
                    segment_name="Seasoned Facility Review & Takeout",
                    finance_product="Competitive Refinance & Restructure",
                    target_role="Finance Director",
                    commercial_hypothesis=(
                        "Incumbent secured facility registered over 36 months ago may carry legacy advance rates, "
                        "service fees, or concentration limits that can be competitively benchmarked."
                    ),
                    supporting_evidence=[
                        f"Outstanding charge registered > 36 months ago on Companies House for {company_name} (CRN {crn}).",
                        "Seasoned UK B2B operators with >= 3 years of clean trading history often qualify for improved advance rates or reduced service margins.",
                    ],
                    observed_facts=[
                        f"Active charge on Companies House register for CRN {crn} has creation date > 36 months prior to snapshot.",
                    ],
                    derived_features=[
                        "indicator:refinance_window",
                        "charge_seasoning_months_gt_36:true",
                    ],
                    invalidation_criteria=[
                        "Charge relates to a multi-year fixed-rate term loan or property mortgage with steep early-repayment penalties.",
                        "Charge was already satisfied in practice but Form MR04 was never filed by the borrower.",
                    ],
                    evidence_ids=ev_ids,
                    confidence_contract=conf,
                    recommended_channel_type="PUBLICATION",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.TRADE_PUBLICATION_ARTICLE,
                    content_theme="Benchmarking Your Working-Capital Facility: Are You Overpaying for Factoring?",
                    campaign_headline="Benchmark Your Working Capital: Compare Advance Rates and Service Fees",
                    executive_message=(
                        "Working-capital facilities aged over 36 months can carry legacy discount margins or concentration caps. "
                        "A confidential facility benchmark review evaluates whether advance rates and fee terms align with current market tiers."
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
                    confidence_score=conf.composite_confidence,
                )
            )

        # -------------------------------------------------------------
        # 4. Strategy: Operational Growth & Debtor Days Working-Capital Pressure
        #    Generated when operational/accounts/payment indicators are present
        # -------------------------------------------------------------
        operational_indicators = {
            "dbt_increase",
            "headcount_surge",
            "accounts_balance_sheet_extracted",
            "late_payment_pressure",
            "working_capital_expansion",
        }
        matched_ops = [ind for ind in clean_indicators if ind in operational_indicators]
        if matched_ops:
            ch_name = _find_channel("PUBLICATION", "UK Commercial Finance & Sector Benchmark Digest")
            conf = self._build_confidence_contract(
                source_reliability=0.84,
                entity_resolution_confidence=entity_resolution_confidence,
                evidence_completeness=0.78,
                hypothesis_confidence=0.72,
                verification_status=verification_status,
                data_freshness_status=data_freshness_status,
            )
            strategies.append(
                CampaignStrategy(
                    segment_name="Expanding B2B Operators & Extended Debtor Cycles",
                    finance_product="Selective Invoice Finance & Trade Working Capital",
                    target_role="Finance Director / Managing Director",
                    commercial_hypothesis=(
                        f"Observed operational indicators ({', '.join(matched_ops)}) suggest growing working-capital "
                        f"absorption across trade debtors and payroll ahead of customer cash collection."
                    ),
                    supporting_evidence=[
                        f"Operational and filing indicators recorded for {company_name} (CRN {crn}): {', '.join(matched_ops)}.",
                        "Selective debtor finance bridges extended B2B payment terms without requiring whole-turnover lock-in.",
                    ],
                    observed_facts=[
                        f"Triggered operational/filing indicators for CRN {crn}: {', '.join(matched_ops)}.",
                    ],
                    derived_features=[f"indicator:{ind}" for ind in matched_ops],
                    invalidation_criteria=[
                        "Latest statutory balance sheet shows substantial surplus cash reserves exceeding working-capital requirements.",
                        "Observed indicator stems from seasonal or one-off extraordinary accounting items.",
                    ],
                    evidence_ids=ev_ids,
                    confidence_contract=conf,
                    recommended_channel_type="PUBLICATION",
                    recommended_channel_name=ch_name,
                    content_format=ContentFormat.SECTOR_BENCHMARK_GUIDE,
                    content_theme="Managing Debtor Days and Payroll Expansion in UK Mid-Market Supply Chains",
                    campaign_headline="Bridge Extended Customer Payment Terms Without Diluting Equity",
                    executive_message=(
                        f"As {company_name} manages supply chain and operational demands, extended customer payment cycles "
                        f"can tie up liquidity. Indicative selective working-capital lines up to £{indicative_facility_gbp:,.0f} "
                        f"unlock cash from approved B2B invoices."
                    ),
                    compliance_guardrails=COMPLIANCE_GUARDRAILS_DEFAULT,
                    validation_test=ValidationTestPlan(
                        test_name="Sector Working-Capital Benchmark Guide Download",
                        max_test_budget_gbp=0.00,
                        duration_weeks=4,
                        success_criteria=">= 3 benchmark guide downloads from verified target sector FDs",
                        failure_stop_criteria="Zero benchmark downloads within 28 days",
                    ),
                    estimated_lead_time_days=14,
                    confidence_score=conf.composite_confidence,
                )
            )

        return strategies
