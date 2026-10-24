"""
Deterministic iXBRL / XHTML Accounts Parser for Companies House Digital Filings.
Extracts Debtors, Cash, Creditors, and Net Current Assets at zero LLM token cost.
Handles namespace fragmentation and accounting parentheses formatting.
"""

import re
from typing import Optional, Tuple
from bs4 import BeautifulSoup

from backend.app.schemas.warden import BalanceSheetData


def clean_numeric_text(text: Optional[str]) -> Optional[float]:
    """Parse statutory accounting figures, handling commas, currencies, and parentheses."""
    if not text:
        return None

    cleaned = text.strip()
    # Check for parentheses indicating negative value e.g. (250,000)
    is_negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        is_negative = True
        cleaned = cleaned[1:-1].strip()
    elif cleaned.startswith("-"):
        is_negative = True
        cleaned = cleaned[1:].strip()

    # Remove currency symbols and formatting commas
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None

    try:
        val = float(cleaned)
        return -val if is_negative else val
    except ValueError:
        return None


class IXBRLParser:
    """Namespace-agnostic parser for inline XBRL UK statutory accounts."""

    @staticmethod
    def parse(content: bytes) -> Optional[BalanceSheetData]:
        """Parse raw XHTML/iXBRL bytes into a validated BalanceSheetData record."""
        try:
            soup = BeautifulSoup(content, "html.parser")
        except Exception:
            return None

        # Search for tags regardless of XML namespace (e.g. uk-gaap, frc, uk-bus)
        elements = soup.find_all(re.compile(r"nonfraction", re.IGNORECASE))

        raw_map = {}
        for el in elements:
            name_attr = el.get("name", "")
            # Extract local name part after colon if present
            local_name = name_attr.split(":")[-1].lower() if ":" in name_attr else name_attr.lower()
            val = clean_numeric_text(el.text)
            if val is not None and local_name not in raw_map:
                raw_map[local_name] = val

        # Key indicators
        debtors = None
        for key in ("debtors", "tradeandotherreceivables", "tradereceivables"):
            if key in raw_map:
                debtors = abs(raw_map[key])
                break

        cash = None
        for key in ("cashbankinhand", "cashandcashequivalents", "cash"):
            if key in raw_map:
                cash = abs(raw_map[key])
                break

        current_assets = None
        for key in ("currentassets", "totalcurrentassets"):
            if key in raw_map:
                current_assets = abs(raw_map[key])
                break

        # If current_assets total not explicitly tagged, sum components if available
        if current_assets is None and (debtors is not None or cash is not None):
            current_assets = (debtors or 0.0) + (cash or 0.0)

        creditors_1yr = None
        for key in ("creditorsduewithinoneyear", "creditorsdueone", "othercreditors"):
            if key in raw_map:
                creditors_1yr = abs(raw_map[key])
                break

        net_current_assets = None
        for key in ("netcurrentassetsliabilities", "netcurrentassets"):
            if key in raw_map:
                net_current_assets = raw_map[key]
                break

        if current_assets is None or creditors_1yr is None:
            # Insufficient core data in iXBRL
            return None

        if net_current_assets is None:
            net_current_assets = current_assets - creditors_1yr

        # Verify mathematical identity
        expected_net = current_assets - creditors_1yr
        variance = abs(expected_net - net_current_assets)
        is_valid = variance <= 2.0  # £2 rounding tolerance

        framework = "FRS_102" if debtors is not None else "FRS_105"

        return BalanceSheetData(
            extraction_path="IXBRL",
            accounting_framework=framework,
            period_end_date=None,
            current_assets_total=current_assets,
            debtors=debtors,
            cash_at_bank=cash,
            creditors_due_within_one_year=creditors_1yr,
            net_current_assets=net_current_assets,
            is_arithmetic_valid=is_valid,
            rounding_variance=round(variance, 2),
        )
