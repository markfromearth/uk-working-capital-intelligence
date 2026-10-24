"""
Multi-Product Opportunity Matcher.

Maps genuine Companies House indicators (from the Stage 1 audit trail) onto the
client's finance product shelf. A B2B company is *surfaced* as a lead whenever it
displays at least one triggered statutory indicator - regardless of whether it
clears the invoice-discounting score gate - because the indicator may be relevant
to a different product.

Every product match carries the triggering indicators, the statutory evidence text
and the primary source references. No product is ever matched on sector alone.

To tailor the shelf to the client, edit PRODUCT_CATALOG below.
"""

from typing import Any, Dict, List, Optional, Sequence

from backend.app.schemas.warden import ScreeningResult

# Indicators that do not, on their own, constitute an opportunity signal
NON_SIGNAL_INDICATORS = {"b2b_sector_fit", "hard_knockout"}

PRODUCT_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "INVOICE_DISCOUNTING",
        "name": "Confidential Invoice Discounting",
        "short": "Invoice Discounting",
        "indicators": ["white_space", "refinance_window", "recent_event_velocity", "incumbent_lender_facility"],
        "why": "Sales ledger is unencumbered, recently released, or held under a seasoned facility that can be refinanced.",
    },
    {
        "code": "FACTORING",
        "name": "Invoice Factoring (Managed Credit Control)",
        "short": "Factoring",
        "indicators": ["tm01_cluster", "admin_delinquency", "aa01_date_extension"],
        "why": "Governance or administrative strain suggests value in outsourced credit control and faster cash conversion.",
    },
    {
        "code": "ASSET_FINANCE",
        "name": "Asset Finance & Asset Refinance",
        "short": "Asset Finance",
        "indicators": ["asset_finance_footprint", "recent_event_velocity"],
        "why": "Asset-backed charges or recent charge activity indicate capex demand or equity that can be released from owned assets.",
    },
    {
        "code": "REFINANCE",
        "name": "Refinance & Term Debt Restructure",
        "short": "Refinance",
        "indicators": ["refinance_window", "incumbent_lender_facility", "aa01_date_extension"],
        "why": "Seasoned incumbent facilities or reporting-date changes indicate a facility review or restructure window.",
    },
    {
        "code": "GROWTH_FINANCE",
        "name": "Growth & Acquisition Finance",
        "short": "Growth Finance",
        "indicators": ["sh01_capital_allotment", "recent_event_velocity", "contract_award_growth"],
        "why": "Fresh equity allotments, public contract wins, or new security registrations indicate an expansion or acquisition phase that typically needs matched debt.",
    },
    {
        "code": "CONTRACT_FINANCE",
        "name": "Contract & Mobilization Working Capital",
        "short": "Contract Finance",
        "indicators": ["contract_award_growth"],
        "why": "Significant public procurement contract award creates fulfillment lag and upfront supplier mobilization capital requirements.",
    },
    {
        "code": "ABL_TURNAROUND",
        "name": "Asset-Based Lending (Turnaround / Special Situations)",
        "short": "ABL / Turnaround",
        "indicators": ["aa01_date_extension", "tm01_cluster", "admin_delinquency"],
        "why": "Intra-year distress markers suggest a need for flexible, collateral-led liquidity.",
    },
    {
        "code": "TRADE_FINANCE",
        "name": "Trade & Import Finance",
        "short": "Trade Finance",
        "sic_prefixes": ["46", "52"] + [f"{i:02d}" for i in range(10, 34)],
        "why": "Wholesale, freight-forwarding or manufacturing activity with a live statutory signal - supplier-payment and stock funding fit.",
    },
    {
        "code": "CONSTRUCTION_FINANCE",
        "name": "Construction & Contract Finance",
        "short": "Construction Finance",
        "sic_prefixes": ["41", "42", "43"],
        "why": "Contracting business with a live statutory signal - applications-for-payment and retention funding fit.",
    },
    {
        "code": "RECRUITMENT_FINANCE",
        "name": "Recruitment & Payroll Finance",
        "short": "Payroll Finance",
        "sic_prefixes": ["78"],
        "why": "Staffing business with a live statutory signal - weekly payroll funding against timesheets fits.",
    },
]

INDICATOR_LABELS = {
    "white_space": "White Space (ledger released)",
    "refinance_window": "Refinance Window (24-48m facility)",
    "recent_event_velocity": "Recent Charge Activity (90d)",
    "sh01_capital_allotment": "Share Allotment (SH01)",
    "aa01_date_extension": "Accounting Date Change (AA01)",
    "tm01_cluster": "Director Resignations (TM01)",
    "admin_delinquency": "Administrative Delinquency",
    "incumbent_lender_facility": "Incumbent Lender (36m+)",
    "asset_finance_footprint": "Asset Finance Footprint",
}


def triggered_indicators(screening: ScreeningResult) -> List[str]:
    """Return the triggered opportunity indicators (excludes sector fit / knockouts)."""
    out: List[str] = []
    for a in screening.audit_trail:
        if a.triggered and a.indicator not in NON_SIGNAL_INDICATORS and a.indicator not in out:
            out.append(a.indicator)
    return out


def is_surfaced(screening: ScreeningResult) -> bool:
    """A lead is surfaced if it is B2B, not knocked out, and shows >= 1 genuine indicator."""
    return bool(
        screening.is_b2b_eligible
        and not screening.knockout_applied
        and len(triggered_indicators(screening)) > 0
    )


def _sector_match(sic_codes: Sequence[str], prefixes: Sequence[str]) -> Optional[str]:
    for sic in sic_codes or []:
        s = str(sic).strip()
        if any(s.startswith(p) for p in prefixes):
            return s
    return None


def match_products(
    screening: ScreeningResult,
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Match a screened company against the product catalog using only triggered indicators."""
    if not is_surfaced(screening):
        return []

    catalog = catalog or PRODUCT_CATALOG
    signals = triggered_indicators(screening)
    evidence = {
        a.indicator: a for a in screening.audit_trail if a.triggered and a.indicator in signals
    }

    matches: List[Dict[str, Any]] = []
    for product in catalog:
        hits: List[str] = []
        sector_sic = None
        if product.get("indicators"):
            hits = [i for i in product["indicators"] if i in signals]
            if not hits:
                continue
        if product.get("sic_prefixes"):
            sector_sic = _sector_match(screening.sic_codes, product["sic_prefixes"])
            if not sector_sic:
                continue
            if not hits:
                hits = list(signals)  # sector products require >=1 live signal (guaranteed by is_surfaced)

        sources = []
        for h in hits:
            src = evidence[h].primary_source
            if src and src.uri and all(s["uri"] != src.uri or s["source_id"] != src.source_id for s in sources):
                sources.append(src.model_dump())

        matches.append(
            {
                "code": product["code"],
                "name": product["name"],
                "short": product.get("short", product["name"]),
                "fit_strength": len(hits) + (1 if sector_sic else 0),
                "triggering_indicators": hits,
                "sector_sic": sector_sic,
                "rationale": product["why"],
                "evidence": [evidence[h].evidence for h in hits],
                "primary_sources": sources,
            }
        )

    matches.sort(key=lambda m: m["fit_strength"], reverse=True)
    return matches
