#!/usr/bin/env python3
"""
Verification Script for Companies House Invoice Finance Warden (CH-Only MVP).
Runs end-to-end evaluation of a company:
- Stage 1: Structured screening and 100-pt provisional scoring.
- Stage 2: Quant accounts balance sheet extraction & trapped cash sizing.
"""

import asyncio
import os
import sys

# Ensure repository root is on sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from backend.app.services.pipeline_orchestrator import default_pipeline_orchestrator
from backend.app.schemas.warden import LendingArchetype


async def main():
    target_crn = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "08421099"
    use_demo = "--demo" in sys.argv or target_crn.upper() in ("DEMO", "SAMPLE")

    if use_demo:
        target_crn = "01234567"
        default_pipeline_orchestrator.ch_client.register_fixture(
            target_crn,
            {
                "profile": {
                    "company_number": target_crn,
                    "company_name": "TITAN FREIGHT & LOGISTICS LTD",
                    "company_status": "active",
                    "sic_codes": ["49410"],  # Freight transport by road
                    "accounts": {"overdue": False},
                    "confirmation_statement": {"overdue": False},
                },
                "charges": [
                    {
                        "charge_code": "001",
                        "status": "fully-satisfied",
                        "satisfied_on": "2026-04-10",
                        "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
                    }
                ],
                "filing_history": [
                    {
                        "category": "accounts",
                        "type": "AA",
                        "date": "2026-01-15",
                        "links": {"document_metadata": "https://document-api.company-information.service.gov.uk/document/doc123"},
                    }
                ],
            },
        )

    print(f"\n=======================================================")
    print(f"  INVOICE FINANCE WARDEN (CH-ONLY MVP VERIFICATION)   ")
    print(f"=======================================================\n")
    print(f"[*] Target CRN: {target_crn} {'(DEMO FIXTURE)' if use_demo else '(LIVE LOOKUP)'}")
    print(f"[*] Mode: Companies House Statutory Data Pipeline (100% Free Public Data)")
    print(f"[*] Executing Stage 1 (Screening & Scoring) & Stage 2 (Quant Sizing)...\n")

    threshold = 45.0 if use_demo else 50.0
    for arg in sys.argv:
        if arg.startswith("--threshold="):
            threshold = float(arg.split("=")[1])

    result = await default_pipeline_orchestrator.process_company(
        crn=target_crn,
        archetype=LendingArchetype.PRIME_INVOICE_DISCOUNTING,
        min_gate_threshold=threshold,
    )

    status = result.get("status")
    print(f"[>] Pipeline Execution Status: {status}")

    screening = result.get("screening")
    if screening:
        print(f"\n--- STAGE 1: STRUCTURED SCREENING & SCORING ---")
        print(f"Company Name:       {screening.company_name}")
        print(f"SIC Codes:          {screening.sic_codes}")
        print(f"B2B Trade Eligible: {screening.is_b2b_eligible}")
        print(f"Provisional Score:  {screening.provisional_score} / 100.0 pts")
        print(f"Passed Gate:        {screening.passed_gate}")
        print(f"Knockout Applied:   {screening.knockout_applied}")
        if screening.knockout_reason:
            print(f"Knockout Reason:    {screening.knockout_reason}")

        print(f"\n[Audit Trail Breakdown]")
        for item in screening.audit_trail:
            status_icon = "✓" if item.triggered else "✗"
            print(f"  {status_icon} {item.indicator.ljust(22)}: {item.awarded} pts (max {item.weight} pts) -> {item.evidence}")

    dossier = result.get("dossier")
    if dossier:
        bs = dossier.balance_sheet
        print(f"\n--- STAGE 2: QUANT ACCOUNTS EXTRACTION & SIZING ---")
        print(f"Extraction Path:    {bs.extraction_path} ({bs.accounting_framework})")
        print(f"Current Assets:     £{bs.current_assets_total:,.2f}")
        print(f"Reported Debtors:   {f'£{bs.debtors:,.2f}' if bs.debtors else 'Not broken out (FRS 105)'}")
        print(f"Cash at Bank:       {f'£{bs.cash_at_bank:,.2f}' if bs.cash_at_bank else 'N/A'}")
        print(f"Creditors (< 1 yr): £{bs.creditors_due_within_one_year:,.2f}")
        print(f"Net Current Assets: £{bs.net_current_assets:,.2f}")
        print(f"Math Validated:     {bs.is_arithmetic_valid} (Variance: £{bs.rounding_variance})")

        print(f"\n[Commercial Facility Sizing]")
        print(f"Eligible Book Debts:    £{dossier.eligible_book_debts:,.2f}")
        print(f"Indicative 80% Advance: £{dossier.indicative_facility_80pct:,.2f}")
        print(f"Trapped Cash Release:   £{dossier.trapped_cash_release:,.2f}")

        print(f"\n[Underwriting Sales Pitch]")
        print(f"\"{dossier.commercial_pitch}\"")
    else:
        print(f"\n[*] Stage 2 Bypassed (Company did not pass Stage 1 Gate or accounts unavailable).")

    print(f"\n=======================================================")
    print(f"  VERIFICATION RUN COMPLETE")
    print(f"=======================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
