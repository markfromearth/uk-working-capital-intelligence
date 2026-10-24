"""
Unit and integration tests for Stage 2: Dual-Path Quant Accounts Extractor & Trapped Cash Sizer.
Verifies iXBRL parsing, PyMuPDF page pruning, deterministic math validation, and facility sizing.
"""

import pytest
import fitz  # PyMuPDF
from backend.app.schemas.warden import BalanceSheetData, ScreeningResult
from backend.app.services.accounts_fetcher import AccountsFetcher
from backend.app.services.ixbrl_parser import IXBRLParser, clean_numeric_text
from backend.app.services.pdf_vision_extractor import PDFVisionExtractor
from backend.app.services.quant_math import QuantMathEngine, get_sector_debtor_factor


def test_clean_numeric_text():
    assert clean_numeric_text("£450,000") == 450000.0
    assert clean_numeric_text("(260,000)") == -260000.0
    assert clean_numeric_text("-150.50") == -150.50
    assert clean_numeric_text("None") is None
    assert clean_numeric_text("") is None


def test_ixbrl_parser_valid_accounts():
    sample_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2008/inlineXBRL" xmlns:uk-gaap="http://www.xbrl.org/uk/gaap/core/2009-09-01">
    <body>
        <ix:nonFraction name="uk-gaap:Debtors">420000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CashBankInHand">40000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CurrentAssets">460000</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:CreditorsDueWithinOneYear">(260000)</ix:nonFraction>
        <ix:nonFraction name="uk-gaap:NetCurrentAssetsLiabilities">200000</ix:nonFraction>
    </body>
    </html>""".encode("utf-8")

    result = IXBRLParser.parse(sample_xhtml)
    assert result is not None
    assert result.extraction_path == "IXBRL"
    assert result.debtors == 420000.0
    assert result.cash_at_bank == 40000.0
    assert result.current_assets_total == 460000.0
    assert result.creditors_due_within_one_year == 260000.0
    assert result.net_current_assets == 200000.0
    assert result.is_arithmetic_valid is True
    assert result.accounting_framework == "FRS_102"


def test_pdf_page_pruning_and_extraction():
    # Build a synthetic 3-page PDF:
    # Page 0: Cover / Directors' Report
    # Page 1: Balance Sheet
    # Page 2: Notes
    doc = fitz.open()

    # Page 0
    p0 = doc.new_page()
    p0.insert_text((50, 100), "DIRECTORS' REPORT AND STRATEGIC REPORT\nThe directors present their report...")

    # Page 1 (Balance Sheet)
    p1 = doc.new_page()
    p1.insert_text(
        (50, 100),
        "STATEMENT OF FINANCIAL POSITION / BALANCE SHEET AS AT 31 DEC 2025\n"
        "Current assets: 500000\n"
        "Debtors: 380000\n"
        "Cash at bank: 120000\n"
        "Creditors: amounts falling due within one year: 280000\n"
        "Net current assets: 220000\n"
        "Called up share capital: 100\n"
    )

    # Page 2
    p2 = doc.new_page()
    p2.insert_text((50, 100), "NOTES TO THE FINANCIAL STATEMENTS\n1. Accounting policies...")

    pdf_bytes = doc.write()

    # Test Pruner
    pruned_bytes, page_idx = PDFVisionExtractor.prune_to_balance_sheet(pdf_bytes)
    assert pruned_bytes is not None
    assert page_idx == 1  # Successfully targeted page 1

    # Verify pruned PDF only has 1 page
    pruned_doc = fitz.open(stream=pruned_bytes, filetype="pdf")
    assert len(pruned_doc) == 1

    # Test Extractor
    extracted = PDFVisionExtractor.extract_from_pruned_pdf(pruned_bytes)
    assert extracted is not None
    assert extracted.current_assets_total == 500000.0
    assert extracted.debtors == 380000.0
    assert extracted.creditors_due_within_one_year == 280000.0
    assert extracted.is_arithmetic_valid is True


def test_quant_math_facility_sizing_frs102():
    screening = ScreeningResult(
        crn="08421099",
        company_name="TITAN HAULAGE LIMITED",
        sic_codes=["49410"],
        is_b2b_eligible=True,
        provisional_score=78.5,
        passed_gate=True,
        knockout_applied=False,
    )

    balance_sheet = BalanceSheetData(
        extraction_path="IXBRL",
        accounting_framework="FRS_102",
        current_assets_total=600000.0,
        debtors=480000.0,
        cash_at_bank=50000.0,
        creditors_due_within_one_year=310000.0,
        net_current_assets=290000.0,
        is_arithmetic_valid=True,
    )

    dossier = QuantMathEngine.size_facility(screening, balance_sheet)

    assert dossier.eligible_book_debts == 480000.0
    assert dossier.indicative_facility_80pct == 384000.0  # 480k * 0.8
    assert "releases up to £384,000" in dossier.commercial_pitch
    assert "surplus working capital cushion" in dossier.commercial_pitch


def test_quant_math_facility_sizing_frs105_fallback():
    screening = ScreeningResult(
        crn="11902341",
        company_name="APEX PRECISION ENGINEERING LTD",
        sic_codes=["25620"],  # Manufacturing (70% debtor ratio)
        is_b2b_eligible=True,
        provisional_score=72.0,
        passed_gate=True,
        knockout_applied=False,
    )

    # FRS 105 Micro-entity filing without broken-out debtors
    balance_sheet = BalanceSheetData(
        extraction_path="VISION_PDF",
        accounting_framework="FRS_105",
        current_assets_total=500000.0,
        debtors=None,
        cash_at_bank=None,
        creditors_due_within_one_year=250000.0,
        net_current_assets=250000.0,
        is_arithmetic_valid=True,
    )

    dossier = QuantMathEngine.size_facility(screening, balance_sheet)

    # 500k * 70% = 350k eligible book debts
    assert dossier.eligible_book_debts == 350000.0
    # 350k * 80% = 280k indicative facility
    assert dossier.indicative_facility_80pct == 280000.0
    assert "estimated debtor allocation (70% of current assets under FRS 105)" in dossier.commercial_pitch


def test_accounts_fetcher_metadata_selector():
    fetcher = AccountsFetcher()
    filings = [
        {"type": "CS01", "category": "confirmation-statement"},
        {
            "type": "AA",
            "category": "accounts",
            "links": {"document_metadata": "https://document-api.company-information.service.gov.uk/document/doc123"},
        },
    ]

    selected = fetcher.extract_latest_accounts_metadata(filings)
    assert selected is not None
    assert selected["type"] == "AA"
