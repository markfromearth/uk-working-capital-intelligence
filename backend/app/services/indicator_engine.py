"""
Commercial Indicator Engine for UK Invoice Finance.
Evaluates statutory Companies House filings and charges data to detect:
1. White Space (Unencumbered sales ledgers post-bank debenture satisfaction)
2. Refinance Windows (Secondary invoice finance facilities aged 24-48m)
3. TM01 Director Resignation Clusters (>= 2 exits in 90 days)
4. Administrative Delinquency & Gazette Notices
5. B2B Sector Eligibility via UK SIC codes
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.app.schemas.warden import LenderClassification

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


def is_b2b_eligible(sic_codes: Optional[List[str]]) -> Tuple[bool, str]:
    """Verify if company's primary SIC codes fall within high working-capital B2B trade sectors."""
    if not sic_codes:
        return False, "No SIC codes recorded"

    for sic in sic_codes:
        clean_sic = str(sic).strip()
        if any(clean_sic.startswith(pfx) for pfx in ELIGIBLE_B2B_PREFIXES):
            return True, f"Qualified B2B SIC code matched: {clean_sic}"

    return False, f"SIC codes {sic_codes} belong to retail, hospitality or low invoice volume sectors"


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
) -> Tuple[bool, float, str]:
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
                return (
                    False,
                    0.0,
                    f"Active charge present ({chg.get('charge_code', 'unknown')}); debtor ledger is encumbered",
                )

    # 2. Look for satisfied bank charges
    best_candidate_age = None
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
                            bank_name = c_name

    if best_candidate_age is not None:
        # Exponential decay factor from day 180 to 730
        decay = max(0.2, 1.0 - ((best_candidate_age - 180.0) / 550.0) * 0.8)
        return (
            True,
            decay,
            f"Unencumbered book debts: {bank_name} charge satisfied {int(best_candidate_age)}d ago with no new floating charge",
        )

    return False, 0.0, "No qualifying satisfied High Street bank charges in the 180-730 day window"


def evaluate_refinance_window(
    charges: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
) -> Tuple[bool, float, str]:
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
                        return (
                            True,
                            1.0,
                            f"Refinance candidate: Active charge with {c_name} created {months}m ago (24-48m seasoning window)",
                        )

    return False, 0.0, "No secondary invoice finance charges in the 24-48m refinancing window"


def evaluate_tm01_cluster(
    filings: List[Dict[str, Any]],
    as_of: Optional[datetime] = None,
) -> Tuple[bool, int, str]:
    """
    TM01 Resignation Cluster:
    Detects if 2 or more officers resigned in the trailing 90 days.
    """
    now = as_of or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)
    count = 0

    for f in filings:
        f_type = str(f.get("type", "")).upper()
        f_date = parse_date(f.get("date"))

        if f_type == "TM01" and f_date and f_date >= cutoff:
            count += 1

    if count >= 2:
        return True, count, f"Cluster detected: {count} director resignations (TM01) filed in trailing 90 days"

    return False, count, f"Normal officer stability ({count} TM01 filings in last 90 days)"


def evaluate_admin_delinquency(
    profile: Dict[str, Any],
    filings: List[Dict[str, Any]],
) -> Tuple[bool, bool, str]:
    """
    Evaluates administrative stress and legal gazette notices.
    Returns: (is_delinquent, has_gazette_strikeoff, explanation)
    """
    accounts = profile.get("accounts", {})
    cs = profile.get("confirmation_statement", {})

    acc_overdue = bool(accounts.get("overdue", False))
    cs_overdue = bool(cs.get("overdue", False))

    # Check filings for First Gazette notice (GAZ1 or GAZ2)
    has_gaz1 = False
    for f in filings:
        f_type = str(f.get("type", "")).upper()
        if f_type in ("GAZ1", "GAZ2"):
            has_gaz1 = True
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
    return is_delinquent, strikeoff_or_insolvent, summary
