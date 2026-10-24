"""
Deterministic Financial Math & Trapped Cash Sizing Engine for UK Invoice Finance.
Reconciles balance sheet equations, handles FRS 105 aggregate accounts,
calculates 80% facility borrowing bases, and generates commercial outreach copy.
"""

from typing import List, Optional
from backend.app.schemas.warden import BalanceSheetData, TrappedCashDossier, ScreeningResult


def get_sector_debtor_factor(sic_codes: Optional[List[str]]) -> float:
    """Determine estimated trade debtor proportion of current assets for FRS 105 filings."""
    if not sic_codes:
        return 0.65

    for sic in sic_codes:
        s = str(sic).strip()
        if s.startswith("78") or s.startswith("62"):  # Recruitment / IT
            return 0.80
        if s.startswith("49") or s.startswith("50") or s.startswith("51") or s.startswith("52"):  # Transport
            return 0.70
        if any(s.startswith(f"{i:02d}") for i in range(10, 34)):  # Manufacturing
            return 0.70
        if s.startswith("46"):  # Wholesale
            return 0.75
        if s.startswith("41") or s.startswith("42") or s.startswith("43"):  # Construction
            return 0.55

    return 0.65


class QuantMathEngine:
    """Sizes commercial facility borrowing base and formulates underwriting pitch."""

    @classmethod
    def size_facility(
        cls,
        screening: ScreeningResult,
        balance_sheet: BalanceSheetData,
    ) -> TrappedCashDossier:
        """Calculate indicative advance and construct evidence-grounded broker pitch."""
        # Determine book debts
        if balance_sheet.debtors is not None and balance_sheet.debtors > 0:
            eligible_book_debts = balance_sheet.debtors
            debtor_source = "reported sales ledger (FRS 102)"
        else:
            factor = get_sector_debtor_factor(screening.sic_codes)
            eligible_book_debts = round(balance_sheet.current_assets_total * factor, 2)
            debtor_source = f"estimated debtor allocation ({int(factor*100)}% of current assets under FRS 105)"

        # Standard commercial invoice finance advance rate (80%)
        indicative_facility = round(eligible_book_debts * 0.80, 2)
        creditors = balance_sheet.creditors_due_within_one_year

        # Trapped cash release against short-term trade debt
        trapped_cash_release = indicative_facility

        # Build compelling commercial rationale
        crn = screening.crn
        name = screening.company_name
        score = screening.provisional_score

        pitch_parts = [
            f"{name} (CRN: {crn}) achieved a Provisional Warden Score of {score}/100.",
            f"The business holds £{eligible_book_debts:,.0f} in {debtor_source} against £{creditors:,.0f} in creditors due within one year.",
            f"An 80% confidential invoice discounting facility releases up to £{indicative_facility:,.0f} of immediate liquidity.",
        ]

        if indicative_facility > creditors:
            net_surplus = indicative_facility - creditors
            pitch_parts.append(
                f"This fully satisfies short-term trade liabilities with an estimated £{net_surplus:,.0f} surplus working capital cushion."
            )
        else:
            pitch_parts.append(
                "This significantly offsets current trade creditor pressure and bridges supplier payment terms."
            )

        commercial_pitch = " ".join(pitch_parts)

        return TrappedCashDossier(
            crn=crn,
            company_name=name,
            provisional_score=score,
            balance_sheet=balance_sheet,
            eligible_book_debts=eligible_book_debts,
            indicative_facility_80pct=indicative_facility,
            trapped_cash_release=trapped_cash_release,
            commercial_pitch=commercial_pitch,
        )
