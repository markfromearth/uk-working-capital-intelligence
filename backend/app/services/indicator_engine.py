"""
Commercial Indicator Engine for UK Invoice Finance.
Evaluates statutory Companies House filings and charges data to detect short-to-mid term financing demand:
1. White Space (Unencumbered sales ledgers post-bank debenture satisfaction)
2. Refinance Windows (Secondary invoice finance facilities aged 24-48m)
3. TM01 Director Resignation Clusters (>= 2 exits in 90 days)
4. Administrative Delinquency & Gazette Notices
5. B2B Sector Eligibility via UK SIC codes

Every indicator binds to a retrievable PrimarySourceReference pointing directly to statutory evidence.
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.app.schemas.warden import LenderClassification, PrimarySourceReference

# --- Regex Classifier Patterns ---
HIGH_STREET_BANK_PATTERN = re.compile(
    r"\b(barclays|natwest|national westminster|royal bank of scotland|rbs|lloyds|"
    r"hsbc|santander|bank of scotland|clydesdale|virgin money|tsb bank|yorkshire bank)\b",
    re.IGNORECASE,
)

SECONDARY_IF_PATTERN = re.compile(
    r"\b(bibby|ultimate finance|time finance|novuna|close brothers|aldermore|"
    r"shawbrook|arbuthnot|paragon|leumi|secure trust|kriya|marketfinance|"
    r"skipworth|hitachi capital|metro bank)\b",
    re.IGNORECASE,
)

ALTERNATIVE_PATTERN = re.compile(
    r"\b(iwoca|funding circle|boost capital|capify|youlend|liberis)\b",
    re.IGNORECASE,
)

# Eligible B2B Division Prefixes (SIC 2007)
ELIGIBLE_B2B_PREFIXES = (
    # Manufacturing
    tuple(f"{i:02d}" for i in range(10, 34))
    + ("46",)  # Wholesale trade
    + ("49", "50", "51", "52", "53")  # Transport & logistics
    + ("41", "42", "43")  # Construction & civil engineering
    + ("62",)  # IT & computer programming
    + ("78",)  # Employment placement / staffing
)


def classify_lender(name: Optional[str]) -> LenderClassification:
    """Classify a charge-holder entity name."""
    if not name:
        return LenderClassification.UNCLASSIFIED

    clean_name = name.strip()
    if HIGH_STREET_BANK_PATTERN.search(clean_name):
        return LenderClassification.HIGH_STREET_BANK
    if SECONDARY_IF_PATTERN.search(clean_name):
        return LenderClassification.SECONDARY_INVOICE_FINANCIER
    if ALTERNATIVE_PATTERN.search(clean_name):
        return LenderClassification.ALTERNATIVE_CHALLENGER

    return LenderClassification.UNCLASSIFIED


def is_b2b_eligible(
    sic_codes: Optional[List[str]], crn: str = ""
) -> Tuple[bool, str, Optional[PrimarySourceReference]]:
    """Verify if company's primary SIC codes fall within high working-capital B2B trade sectors."""
    if not sic_codes:
        return False, "No SIC codes recorded", None

    for sic in sic_codes:
        clean_sic = str(sic).strip()
        if any(clean_sic.startswith(pfx) for pfx in ELIGIBLE_B2B_PREFIXES):
            source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_PROFILE",
                source_id=f"SIC_{clean_sic}",
                title=f"Companies House SIC Registration ({clean_sic})",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}" if crn else "https://find-and-update.company-information.service.gov.uk",
                published_date=None,
                excerpt=f"Registered SIC classification: {clean_sic}",
            )
            return True, f"Qualified B2B SIC code matched: {clean_sic}", source

    return False, f"SIC codes {sic_codes} belong to retail, hospitality or low invoice volume sectors", None


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO date string safely."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def evaluate_white_space(
    charges: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, float, str, Optional[PrimarySourceReference]]:
    """
    White Space Trigger:
    Requires:
    1. ZERO active floating charges / debentures currently on the register.
    2. At least one High Street Bank charge satisfied between 180 and 730 days ago.
    """
    now = as_of or datetime.now(timezone.utc)

    # 1. Audit Check: Any active floating charge voids white space
    for chg in charges:
        status = chg.get("status", "").lower()
        if status in ("outstanding", "created", "active"):
            particulars = str(chg.get("particulars", "")).lower()
            if "floating" in particulars or "debenture" in particulars or "all monies" in particulars:
                source = PrimarySourceReference(
                    source_type="COMPANIES_HOUSE_CHARGES",
                    source_id=chg.get("charge_code", str(chg.get("id", "active_charge"))),
                    title=f"Active Charge Register Entry ({chg.get('charge_code', 'Active')})",
                    uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
                    published_date=chg.get("created_on"),
                    excerpt=f"Active debenture registered: {particulars[:100]}",
                )
                return (
                    False,
                    0.0,
                    f"Active charge present ({chg.get('charge_code', 'unknown')}); debtor ledger is encumbered",
                    source,
                )

    # 2. Look for satisfied bank charges
    best_candidate_age = None
    best_charge = None
    bank_name = None

    for chg in charges:
        status = chg.get("status", "").lower()
        sat_date = parse_date(chg.get("satisfied_on"))

        if status in ("fully-satisfied", "satisfied") and sat_date:
            age_days = (now - sat_date).total_seconds() / 86400.0

            if 180.0 <= age_days <= 730.0:
                # Check lender
                persons = chg.get("persons_entitled", [])
                for p in persons:
                    c_name = p.get("name", "")
                    if classify_lender(c_name) == LenderClassification.HIGH_STREET_BANK:
                        if best_candidate_age is None or age_days < best_candidate_age:
                            best_candidate_age = age_days
                            best_charge = chg
                            bank_name = c_name

    if best_candidate_age is not None and best_charge is not None:
        # Exponential decay factor from day 180 to 730
        decay = max(0.2, 1.0 - ((best_candidate_age - 180.0) / 550.0) * 0.8)
        source = PrimarySourceReference(
            source_type="COMPANIES_HOUSE_CHARGES",
            source_id=best_charge.get("charge_code", str(best_charge.get("id", "charge-satisfied"))),
            title=f"Form MR04 Satisfaction of Charge ({bank_name})",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
            published_date=best_charge.get("satisfied_on"),
            excerpt=f"Full satisfaction of charge {best_charge.get('charge_code')} entitled to {bank_name} on {best_charge.get('satisfied_on')}",
        )
        return (
            True,
            decay,
            f"Unencumbered book debts: {bank_name} charge satisfied {int(best_candidate_age)}d ago with no new floating charge",
            source,
        )

    return False, 0.0, "No qualifying satisfied High Street bank charges in the 180-730 day window", None


def evaluate_refinance_window(
    charges: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, float, str, Optional[PrimarySourceReference]]:
    """
    Refinance Window:
    Active charge held with a secondary/alternative invoice financier aged between 24 and 48 months (730 - 1460 days).
    """
    now = as_of or datetime.now(timezone.utc)

    for chg in charges:
        status = chg.get("status", "").lower()
        if status in ("outstanding", "created", "active"):
            created_on = parse_date(chg.get("created_on"))
            if not created_on:
                continue

            age_days = (now - created_on).total_seconds() / 86400.0
            if 730.0 <= age_days <= 1460.0:
                persons = chg.get("persons_entitled", [])
                for p in persons:
                    c_name = p.get("name", "")
                    classification = classify_lender(c_name)
                    if classification in (
                        LenderClassification.SECONDARY_INVOICE_FINANCIER,
                        LenderClassification.ALTERNATIVE_CHALLENGER,
                    ):
                        months = int(age_days / 30.44)
                        source = PrimarySourceReference(
                            source_type="COMPANIES_HOUSE_CHARGES",
                            source_id=chg.get("charge_code", str(chg.get("id", "active_secondary"))),
                            title=f"Active Commercial Debenture ({c_name})",
                            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
                            published_date=chg.get("created_on"),
                            excerpt=f"Active charge registered on {chg.get('created_on')} in favor of {c_name} (Age: {months} months)",
                        )
                        return (
                            True,
                            1.0,
                            f"Refinance candidate: Active charge with {c_name} created {months}m ago (24-48m seasoning window)",
                            source,
                        )

    return False, 0.0, "No secondary invoice finance charges in the 24-48m refinancing window", None


def evaluate_tm01_cluster(
    filings: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, int, str, Optional[PrimarySourceReference]]:
    """
    TM01 Resignation Cluster:
    Detects if 2 or more officers resigned in the trailing 90 days.
    """
    now = as_of or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)
    count = 0
    latest_tm01_date = None

    for f in filings:
        f_type = str(f.get("type", "")).upper()
        f_date = parse_date(f.get("date"))

        if f_type == "TM01" and f_date and f_date >= cutoff:
            count += 1
            if latest_tm01_date is None or f.get("date") > latest_tm01_date:
                latest_tm01_date = f.get("date")

    if count >= 2:
        source = PrimarySourceReference(
            source_type="COMPANIES_HOUSE_FILING",
            source_id="TM01_RESIGNATION_CLUSTER",
            title=f"Form TM01 Director Resignation Filings ({count} exits)",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history" if crn else "",
            published_date=latest_tm01_date,
            excerpt=f"{count} Form TM01 (Termination of Director Appointment) filings recorded within trailing 90 days",
        )
        return True, count, f"Cluster detected: {count} director resignations (TM01) filed in trailing 90 days", source

    return False, count, f"Normal officer stability ({count} TM01 filings in last 90 days)", None


def evaluate_admin_delinquency(
    profile: Dict[str, Any],
    filings: List[Dict[str, Any]],
    crn: str = "",
) -> Tuple[bool, bool, str, Optional[PrimarySourceReference]]:
    """
    Evaluates administrative stress and legal gazette notices.
    Returns: (is_delinquent, has_gazette_strikeoff, explanation, primary_source)
    """
    accounts = profile.get("accounts", {})
    cs = profile.get("confirmation_statement", {})

    acc_overdue = bool(accounts.get("overdue", False))
    cs_overdue = bool(cs.get("overdue", False))

    has_gaz1 = False
    gaz1_date = None
    for f in filings:
        f_type = str(f.get("type", "")).upper()
        if f_type in ("GAZ1", "GAZ2"):
            has_gaz1 = True
            gaz1_date = f.get("date")
            break

    status = profile.get("company_status", "").lower()
    in_liquidation = status in ("liquidation", "receivership", "administration", "dissolved")

    is_delinquent = acc_overdue or cs_overdue
    strikeoff_or_insolvent = has_gaz1 or in_liquidation

    reasons = []
    if acc_overdue:
        reasons.append("Accounts overdue")
    if cs_overdue:
        reasons.append("Confirmation statement overdue")
    if has_gaz1:
        reasons.append("First Gazette notice published (GAZ1)")
    if in_liquidation:
        reasons.append(f"Insolvency/Liquidation status: {status}")

    summary = ", ".join(reasons) if reasons else "Filings current; no gazette or strike-off notices"

    source = None
    if strikeoff_or_insolvent:
        source = PrimarySourceReference(
            source_type="GAZETTE_NOTICE" if has_gaz1 else "COMPANIES_HOUSE_PROFILE",
            source_id="GAZ1_STRIKE_OFF" if has_gaz1 else f"STATUS_{status.upper()}",
            title="First Gazette Notice for Compulsory Strike-Off" if has_gaz1 else f"Corporate Status: {status.capitalize()}",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}" if crn else "",
            published_date=gaz1_date,
            excerpt=summary,
        )
    elif is_delinquent:
        source = PrimarySourceReference(
            source_type="COMPANIES_HOUSE_PROFILE",
            source_id="OVERDUE_STATUTORY_DEADLINE",
            title="Statutory Filing Cadence Delinquency",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}" if crn else "",
            published_date=None,
            excerpt=summary,
        )

    return is_delinquent, strikeoff_or_insolvent, summary, source


def evaluate_aa01_extension(
    filings: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, str, Optional[PrimarySourceReference]]:
    """
    Evaluates Form AA01 / AA02 (Change of Accounting Reference Date).
    Under Section 392 of the Companies Act 2006, extending the accounting period
    is frequently used as an intra-year distress tactic to buy time for overdue audits,
    HMRC deferrals, or debt covenant restructuring.
    """
    now = as_of or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=365)

    for f in filings:
        f_type = str(f.get("type", "")).upper()
        f_date = parse_date(f.get("date"))

        if f_type in ("AA01", "AA02") and f_date and f_date >= cutoff:
            days_ago = int((now - f_date).total_seconds() / 86400.0)
            source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_FILING",
                source_id=f"AA01_EXTENSION_{f.get('date', 'recent')}",
                title="Form AA01 Change of Accounting Reference Date",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history" if crn else "",
                published_date=f.get("date"),
                excerpt=f"Accounting Reference Date extended via Form {f_type} filed on {f.get('date')} ({days_ago}d ago)",
            )
            return (
                True,
                f"Early-warning distress: Accounting Reference Date extended via Form {f_type} {days_ago}d ago (Section 392 extension)",
                source,
            )

    return False, "Standard accounting reference period maintained (no AA01 extension in trailing 12m)", None


def evaluate_sh01_allotments(
    filings: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, int, str, Optional[PrimarySourceReference]]:
    """
    Evaluates Form SH01 (Return of Allotment of Shares).
    Filing of SH01 within the trailing 180 days represents genuine intra-year equity capital
    injected into the business, validating going-concern solvency between annual balance sheets.
    """
    now = as_of or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=180)
    count = 0
    latest_date = None

    for f in filings:
        f_type = str(f.get("type", "")).upper()
        f_date = parse_date(f.get("date"))

        if f_type == "SH01" and f_date and f_date >= cutoff:
            count += 1
            if latest_date is None or f.get("date") > latest_date:
                latest_date = f.get("date")

    if count > 0:
        source = PrimarySourceReference(
            source_type="COMPANIES_HOUSE_FILING",
            source_id="SH01_SHARE_ALLOTMENT",
            title=f"Form SH01 Return of Allotment of Shares ({count} allotment{'s' if count > 1 else ''})",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history" if crn else "",
            published_date=latest_date,
            excerpt=f"{count} Form SH01 share allotment(s) filed in trailing 180 days (Latest: {latest_date})",
        )
        return (
            True,
            count,
            f"Intra-year equity injection: {count} Form SH01 share allotment(s) registered in trailing 180 days (validated mid-year capital expansion)",
            source,
        )

    return False, 0, "No share allotments (SH01) recorded in trailing 180 days", None


def evaluate_recent_event_velocity(
    charges: List[Dict[str, Any]],
    filings: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, float, str, Optional[PrimarySourceReference]]:
    """
    Evaluates high-impact statutory movements within the trailing 90 days.
    Bypasses annual accounts latency by detecting zero-to-90-day actions:
    1. Fresh MR04 satisfaction of bank charges (immediate unencumbered sales ledger).
    2. Recent MR01 debenture registrations (intra-year capital changes).
    """
    now = as_of or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)

    # 1. Check for ultra-recent MR04 charge satisfactions (trailing 90 days)
    for chg in charges:
        sat_date = parse_date(chg.get("satisfied_on"))
        if sat_date and sat_date >= cutoff:
            days_ago = int((now - sat_date).total_seconds() / 86400.0)
            persons = chg.get("persons_entitled", [])
            lender_name = persons[0].get("name", "Lender") if persons else "Charge Holder"
            source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_CHARGES",
                source_id=chg.get("charge_code", str(chg.get("id", "recent_mr04"))),
                title=f"Recent Form MR04 Charge Satisfaction ({lender_name})",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
                published_date=chg.get("satisfied_on"),
                excerpt=f"Form MR04 recorded full satisfaction on {chg.get('satisfied_on')} ({days_ago}d ago)",
            )
            return (
                True,
                1.0,
                f"Ultra-fresh statutory velocity: {lender_name} charge satisfied just {days_ago}d ago. Peak origination window.",
                source,
            )

    # 2. Check for recent MR01 filings in filing history (trailing 90 days)
    for f in filings:
        f_type = str(f.get("type", "")).upper()
        f_date = parse_date(f.get("date"))
        if f_type in ("MR01", "MG01") and f_date and f_date >= cutoff:
            days_ago = int((now - f_date).total_seconds() / 86400.0)
            source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_FILING",
                source_id=f"RECENT_CHARGE_{f.get('date', '90d')}",
                title="Recent Form MR01 Mortgage Registration",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
                published_date=f.get("date"),
                excerpt=f"New Form MR01 charge registered on {f.get('date')} ({days_ago}d ago)",
            )
            return (
                True,
                0.7,
                f"Recent statutory debt activity: Form MR01 registered {days_ago}d ago (intra-year lender engagement)",
                source,
            )

    return False, 0.0, "No high-impact statutory movements (MR01/MR04) within trailing 90 days", None



# ---------------------------------------------------------------------------
# Multi-Product Opportunity Indicators (informational; zero score weight)
# These broaden lead surfacing beyond invoice discounting to the client's
# wider product shelf (asset finance, refinance, term debt) using only
# genuine Companies House charge-register evidence.
# ---------------------------------------------------------------------------

ASSET_FINANCIER_PATTERN = re.compile(
    r"\b(lombard|close brothers|hitachi capital|novuna|siemens financial|bnp paribas leasing|"
    r"investec asset finance|shire leasing|aldermore|paragon|time finance|haydock finance|"
    r"pcl|propel finance|white oak|aldermore|scania finance|volvo financial|daf financial|"
    r"mercedes-benz financial|man financial|jcb finance|john deere financial|cnh industrial capital|"
    r"agco finance|kubota|toyota financial|caterpillar financial|de lage landen|dll|"
    r"leasing|asset finance|hire purchase)\b",
    re.IGNORECASE,
)

_OUTSTANDING_STATUSES = ("outstanding", "created", "active", "part-satisfied")


def _charge_holder_names(chg: Dict[str, Any]) -> List[str]:
    return [p.get("name", "") for p in chg.get("persons_entitled", []) or [] if p.get("name")]


def summarise_charges(charges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise the genuine CH charge register for display (no inference)."""
    outstanding = [c for c in charges if str(c.get("status", "")).lower() in _OUTSTANDING_STATUSES]
    satisfied = [c for c in charges if str(c.get("status", "")).lower() in ("fully-satisfied", "satisfied")]
    holders: List[str] = []
    for c in outstanding:
        for n in _charge_holder_names(c):
            if n not in holders:
                holders.append(n)
    return {
        "total": len(charges),
        "outstanding": len(outstanding),
        "satisfied": len(satisfied),
        "outstanding_holders": holders,
    }


def evaluate_incumbent_lender(
    charges: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, str, Optional[str], Optional[PrimarySourceReference]]:
    """
    Incumbent Lender Facility:
    Outstanding charge held by a High Street bank or secondary/alternative financier
    that has been in place >= 36 months. Indicates an established facility that may be
    due for review, refinance, or competitive takeout.
    Returns (triggered, description, lender_class, source).
    """
    now = as_of or datetime.now(timezone.utc)
    for chg in charges:
        if str(chg.get("status", "")).lower() not in _OUTSTANDING_STATUSES:
            continue
        created_on = parse_date(chg.get("created_on"))
        if not created_on:
            continue
        age_days = (now - created_on).total_seconds() / 86400.0
        if age_days < 1095.0:
            continue
        for name in _charge_holder_names(chg):
            cls = classify_lender(name)
            if cls == LenderClassification.UNCLASSIFIED:
                continue
            years = age_days / 365.25
            source = PrimarySourceReference(
                source_type="COMPANIES_HOUSE_CHARGES",
                source_id=chg.get("charge_code", str(chg.get("id", "incumbent_charge"))),
                title=f"Outstanding Charge ({name})",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
                published_date=chg.get("created_on"),
                excerpt=f"Outstanding charge in favour of {name} registered {chg.get('created_on')} ({years:.1f} years)",
            )
            return (
                True,
                f"Incumbent facility: outstanding charge with {name} in place {years:.1f} years (review/takeout candidate)",
                cls.value,
                source,
            )
    return False, "No seasoned (36m+) outstanding charges held by identified lenders", None, None


def evaluate_asset_finance_charges(
    charges: List[Dict[str, Any]],
    crn: str = "",
) -> Tuple[bool, int, str, Optional[PrimarySourceReference]]:
    """
    Asset Finance Footprint:
    Outstanding charges registered in favour of asset / leasing financiers, or charges whose
    particulars describe specific plant, vehicles or equipment. Indicates an asset-heavy
    business suited to asset finance, asset refinance or equity release.
    """
    hits: List[Dict[str, Any]] = []
    for chg in charges:
        if str(chg.get("status", "")).lower() not in _OUTSTANDING_STATUSES:
            continue
        names = " ".join(_charge_holder_names(chg))
        particulars = str(chg.get("particulars", "")).lower()
        classification = str(chg.get("classification", {}).get("description", "")).lower() if isinstance(chg.get("classification"), dict) else ""
        if ASSET_FINANCIER_PATTERN.search(names) or any(
            kw in particulars or kw in classification
            for kw in ("chattel", "vehicle", "machinery", "equipment", "plant", "hire purchase")
        ):
            hits.append(chg)
    if hits:
        first = hits[0]
        holder = (_charge_holder_names(first) or ["asset financier"])[0]
        source = PrimarySourceReference(
            source_type="COMPANIES_HOUSE_CHARGES",
            source_id=first.get("charge_code", str(first.get("id", "asset_charge"))),
            title=f"Asset-Backed Charge ({holder})",
            uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges" if crn else "",
            published_date=first.get("created_on"),
            excerpt=f"{len(hits)} outstanding asset-backed charge(s); latest holder {holder}",
        )
        return True, len(hits), f"Asset finance footprint: {len(hits)} outstanding asset-backed charge(s) (e.g. {holder})", source
    return False, 0, "No outstanding asset-backed or leasing charges", None


def evaluate_contract_awards(
    signals: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, float, str, Optional[PrimarySourceReference]]:
    """
    Public Procurement Contract Awards (Contracts Finder / FTS OCDS):
    Detects public sector contract wins in the trailing 180 days.
    Winning sizable supply contracts creates operational mobilization lag and working capital demand.
    Returns (triggered, mobilization_working_capital_estimate_gbp, description, primary_source).
    """
    now = as_of or datetime.now(timezone.utc)
    hits: List[Dict[str, Any]] = []

    for sig in signals:
        if sig.get("signal_type") != "contract_award_growth":
            continue
        obs_time = sig.get("observation_timestamp")
        if isinstance(obs_time, str):
            obs_dt = parse_date(obs_time)
        elif isinstance(obs_time, datetime):
            obs_dt = obs_time if obs_time.tzinfo else obs_time.replace(tzinfo=timezone.utc)
        else:
            obs_dt = None

        if obs_dt:
            age_days = (now - obs_dt).total_seconds() / 86400.0
            if age_days > 180.0:
                continue

        hits.append(sig)

    if not hits:
        return False, 0.0, "No public contract awards observed in trailing 180 days", None

    total_value = sum(float(h.get("value_numeric", 0.0) or 0.0) for h in hits)
    # Typical SME mobilization working-capital requirement is 20% of contract value
    est_mobilization_capital = round(total_value * 0.20, 2)
    latest = hits[0]
    ref_uri = latest.get("evidence_reference", f"https://www.contractsfinder.service.gov.uk/notice/{latest.get('original_event_ref', '')}")

    source = PrimarySourceReference(
        source_type="CONTRACTS_FINDER",
        source_id=str(latest.get("original_event_ref", f"CF_{crn}")),
        title=f"UK Public Procurement Contract Award ({latest.get('value_text', 'Award')})",
        uri=ref_uri,
        retrieval_method="REST_API",
        published_date=str(latest.get("observation_timestamp", ""))[:10],
        excerpt=f"Total awarded contract value: £{total_value:,.0f}. Estimated working capital requirement: £{est_mobilization_capital:,.0f}.",
    )
    desc = f"Won {len(hits)} public contract(s) (£{total_value:,.0f} total). Estimated mobilization funding demand: £{est_mobilization_capital:,.0f}."
    return True, est_mobilization_capital, desc, source


def evaluate_gazette_insolvency(
    signals: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
    crn: str = "",
) -> Tuple[bool, str, Optional[PrimarySourceReference]]:
    """
    The Gazette Statutory Corporate Insolvency Notices:
    Detects official winding-up petitions, appointment of administrators/liquidators,
    or creditors' voluntary liquidation notices in the trailing 365 days.
    Acts as a definitive disqualifying knockout for prime financing.
    Returns (triggered, description, primary_source).
    """
    now = as_of or datetime.now(timezone.utc)
    for sig in signals:
        if sig.get("signal_type") != "gazette_insolvency_notice":
            continue
        obs_time = sig.get("observation_timestamp")
        if isinstance(obs_time, str):
            obs_dt = parse_date(obs_time)
        elif isinstance(obs_time, datetime):
            obs_dt = obs_time if obs_time.tzinfo else obs_time.replace(tzinfo=timezone.utc)
        else:
            obs_dt = None

        if obs_dt:
            age_days = (now - obs_dt).total_seconds() / 86400.0
            if age_days > 365.0:
                continue

        notice_text = sig.get("value_text", "Statutory Insolvency Notice")
        ref_uri = sig.get("evidence_reference", "https://www.thegazette.co.uk")
        notice_id = str(sig.get("original_event_ref", "GAZETTE_NOTICE"))

        source = PrimarySourceReference(
            source_type="GAZETTE_NOTICE",
            source_id=notice_id,
            title="The Gazette Statutory Insolvency Notice",
            uri=ref_uri,
            retrieval_method="PUBLIC_WEB",
            published_date=str(obs_dt)[:10] if obs_dt else None,
            excerpt=f"Published statutory notice: {notice_text}",
        )
        return True, f"Active statutory insolvency notice in The Gazette: {notice_text}", source

    return False, "No active corporate insolvency notices published in The Gazette", None

