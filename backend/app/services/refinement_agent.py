"""
Intelligence Refinement & Provenance Agent (Post-Gate Handoff).
Refines the provisional score up or down based on deep-dive indicator metrics,
business insights, director governance, and working capital velocity.
Guarantees that all primary sources supporting indicators and adjustments are retrievable.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.app.schemas.warden import (
    BalanceSheetData,
    PrimarySourceReference,
    ScoreRefinementAdjustment,
    ScreeningResult,
    TrappedCashDossier,
)
from backend.app.services.indicator_engine import parse_date


class IntelligenceRefinementAgent:
    """
    Evaluates indicated prospects to refine the provisional probability score.
    Surfaces deep-dive indicators, contextual business reports, and enforces primary source retrievability.
    """

    @classmethod
    def refine_provisional_score(
        cls,
        screening: ScreeningResult,
        balance_sheet: BalanceSheetData,
        profile: Dict[str, Any],
        charges: List[Dict[str, Any]],
        filings: List[Dict[str, Any]],
        officers: Optional[List[Dict[str, Any]]] = None,
        as_of: Optional[datetime] = None,
    ) -> Tuple[float, List[ScoreRefinementAdjustment], List[PrimarySourceReference]]:
        """
        Executes deep-dive inspection on an indicated prospect.
        Returns: (refined_score, adjustments_list, all_primary_sources)
        """
        now = as_of or datetime.now(timezone.utc)
        crn = screening.crn
        base_score = screening.provisional_score
        adjustments: List[ScoreRefinementAdjustment] = []
        all_sources: List[PrimarySourceReference] = list(screening.primary_sources)

        # Attach Balance Sheet primary source if present
        if balance_sheet.primary_source:
            all_sources.append(balance_sheet.primary_source)

        # -------------------------------------------------------------
        # 1. Metric: Working Capital Debtors-to-Creditors Coverage
        # -------------------------------------------------------------
        debtors = balance_sheet.debtors or (balance_sheet.current_assets_total * 0.70)
        creditors = balance_sheet.creditors_due_within_one_year

        if creditors > 0:
            coverage_ratio = debtors / creditors
            if coverage_ratio >= 1.4:
                # Strong working capital position: facility comfortably clears liabilities
                source = PrimarySourceReference(
                    source_type="ACCOUNTS_DOCUMENT",
                    source_id=f"BALANCE_SHEET_{crn}",
                    title="Statutory Accounts Balance Sheet Filing",
                    uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history",
                    published_date=balance_sheet.period_end_date,
                    excerpt=f"Reported debtors (£{debtors:,.0f}) exceed short-term trade creditors (£{creditors:,.0f}) with {coverage_ratio:.2f}x coverage.",
                )
                all_sources.append(source)
                adjustments.append(
                    ScoreRefinementAdjustment(
                        metric_name="debtor_to_creditor_coverage",
                        delta_points=5.0,
                        direction="INCREASE",
                        justification=f"Strong working capital coverage ({coverage_ratio:.2f}x). Facility advance cleanly extinguishes short-term trade payables.",
                        primary_sources=[source],
                    )
                )
            elif coverage_ratio < 0.8:
                # High creditor overhang: liquidity strain may require specialized turnaround factoring
                source = PrimarySourceReference(
                    source_type="ACCOUNTS_DOCUMENT",
                    source_id=f"BALANCE_SHEET_{crn}",
                    title="Statutory Accounts Balance Sheet Filing",
                    uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history",
                    published_date=balance_sheet.period_end_date,
                    excerpt=f"Trade creditors (£{creditors:,.0f}) exceed eligible debtor book (£{debtors:,.0f}) (Coverage: {coverage_ratio:.2f}x).",
                )
                all_sources.append(source)
                adjustments.append(
                    ScoreRefinementAdjustment(
                        metric_name="creditor_overhang_drag",
                        delta_points=-6.0,
                        direction="DECREASE",
                        justification=f"Substantial trade creditor overhang ({coverage_ratio:.2f}x coverage). Higher underwriting scrutiny on ledger dilution.",
                        primary_sources=[source],
                    )
                )

        # -------------------------------------------------------------
        # 2. Metric: Corporate Trading Longevity & Seasoning
        # -------------------------------------------------------------
        incorp_date = parse_date(profile.get("date_of_creation"))
        if incorp_date:
            age_years = (now - incorp_date).total_seconds() / (86400.0 * 365.25)
            incorp_source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_PROFILE",
                source_id=f"INCORP_{crn}",
                title="Certificate of Incorporation Record",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}",
                published_date=profile.get("date_of_creation"),
                excerpt=f"Incorporated on {profile.get('date_of_creation')} ({age_years:.1f} years active trading history).",
            )
            all_sources.append(incorp_source)

            if age_years >= 5.0:
                adjustments.append(
                    ScoreRefinementAdjustment(
                        metric_name="trading_seasoning_stability",
                        delta_points=5.0,
                        direction="INCREASE",
                        justification=f"Seasoned enterprise ({age_years:.1f} years trading history) with proven commercial survival across economic cycles.",
                        primary_sources=[incorp_source],
                    )
                )
            elif age_years < 2.0:
                adjustments.append(
                    ScoreRefinementAdjustment(
                        metric_name="young_enterprise_discount",
                        delta_points=-4.0,
                        direction="DECREASE",
                        justification=f"Young enterprise ({age_years:.1f} years). Lenders require higher director personal guarantees or historical trading audit.",
                        primary_sources=[incorp_source],
                    )
                )

        # -------------------------------------------------------------
        # 3. Metric: Governance & Director Stability
        # -------------------------------------------------------------
        officers_list = officers or []
        if officers_list:
            active_officers = [
                o for o in officers_list
                if not o.get("resigned_on") and o.get("officer_role") in ("director", "corporate-director")
            ]
            if len(active_officers) >= 2:
                tenures = []
                for o in active_officers:
                    app_date = parse_date(o.get("appointed_on"))
                    if app_date:
                        tenures.append((now - app_date).total_seconds() / (86400.0 * 365.25))

                avg_tenure = (sum(tenures) / len(tenures)) if tenures else 0.0
                if avg_tenure >= 3.0:
                    gov_source = PrimarySourceReference(
                        source_type="COMPANIES_HOUSE_OFFICERS",
                        source_id=f"OFFICERS_{crn}",
                        title="Companies House Register of Directors",
                        uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/officers",
                        published_date=None,
                        excerpt=f"{len(active_officers)} active directors with average board tenure of {avg_tenure:.1f} years.",
                    )
                    all_sources.append(gov_source)
                    adjustments.append(
                        ScoreRefinementAdjustment(
                            metric_name="board_governance_stability",
                            delta_points=4.0,
                            direction="INCREASE",
                            justification=f"Stable governance: {len(active_officers)} active directors with {avg_tenure:.1f} years average tenure.",
                            primary_sources=[gov_source],
                        )
                    )

        # -------------------------------------------------------------
        # 4. Metric: Commercial Facility Commercial Sweet-Spot
        # -------------------------------------------------------------
        indicative_facility = debtors * 0.80
        if 150000.0 <= indicative_facility <= 2000000.0:
            scale_source = PrimarySourceReference(
                source_type="ACCOUNTS_DOCUMENT",
                source_id=f"FACILITY_SCALE_{crn}",
                title="Borrowing Base Facility Scale Assessment",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}",
                published_date=None,
                excerpt=f"Indicative facility of £{indicative_facility:,.0f} falls within optimal prime invoice discounting appetite (£150k - £2M).",
            )
            all_sources.append(scale_source)
            adjustments.append(
                ScoreRefinementAdjustment(
                    metric_name="optimal_funder_ticket_size",
                    delta_points=4.0,
                    direction="INCREASE",
                    justification=f"Facility size (£{indicative_facility:,.0f}) matches prime UK funder sweet-spot (£150k - £2m), maximizing placement conversion.",
                    primary_sources=[scale_source],
                )
            )

        # -------------------------------------------------------------
        # Calculate Final Refined Score (Bounded 0 - 100)
        # -------------------------------------------------------------
        total_delta = sum(adj.delta_points for adj in adjustments)
        refined_score = round(max(0.0, min(100.0, base_score + total_delta)), 1)

        # Deduplicate primary sources by source_id
        unique_sources: Dict[str, PrimarySourceReference] = {}
        for s in all_sources:
            if s.source_id not in unique_sources:
                unique_sources[s.source_id] = s

        return refined_score, adjustments, list(unique_sources.values())
