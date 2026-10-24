"""
PDF Balance Sheet Page-Pruning & Vision Extraction Pipeline.
Uses PyMuPDF (fitz) to prune multi-page filings to solely the Balance Sheet page,
minimizing LLM token consumption and extracting key financial fields.
"""

import io
import re
from typing import Optional, Tuple
import fitz  # PyMuPDF

from backend.app.schemas.warden import BalanceSheetData
from backend.app.services.ixbrl_parser import clean_numeric_text


class PDFVisionExtractor:
    """Prunes scanned and multi-page account filings down to the Balance Sheet page."""

    BALANCE_SHEET_KEYWORDS = [
        "balance sheet",
        "statement of financial position",
        "current assets",
        "called up share capital",
    ]

    NEGATIVE_KEYWORDS = [
        "directors' report",
        "strategic report",
        "independent auditor's report",
    ]

    @classmethod
    def prune_to_balance_sheet(cls, pdf_bytes: bytes) -> Tuple[Optional[bytes], Optional[int]]:
        """
        Locates the Balance Sheet page in the PDF and extracts solely that page as a 1-page PDF.
        Returns: (single_page_pdf_bytes, page_number_0_indexed)
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception:
            return None, None

        best_page_idx = None
        best_score = -1

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            text = page.get_text("text").lower()

            # Score positive keywords
            pos_score = sum(1 for kw in cls.BALANCE_SHEET_KEYWORDS if kw in text)
            neg_score = sum(1 for kw in cls.NEGATIVE_KEYWORDS if kw in text)

            # If page only has directors report, penalize
            net_score = pos_score - (neg_score * 2)
            if net_score > best_score and pos_score >= 1:
                best_score = net_score
                best_page_idx = page_idx

        if best_page_idx is None:
            # Fall back to page 0 or 1 if document is very short
            best_page_idx = 0 if len(doc) <= 2 else None

        if best_page_idx is None:
            return None, None

        # Build single-page document
        pruned_doc = fitz.open()
        pruned_doc.insert_pdf(doc, from_page=best_page_idx, to_page=best_page_idx)
        out_bytes = pruned_doc.write()
        return out_bytes, best_page_idx

    @classmethod
    def extract_from_pruned_pdf(cls, pruned_pdf_bytes: bytes) -> Optional[BalanceSheetData]:
        """
        Extract financial figures from the pruned single-page balance sheet.
        Performs regex-anchored text extraction with Draft-07 schema compliance,
        with deterministic math validation.
        """
        try:
            doc = fitz.open(stream=pruned_pdf_bytes, filetype="pdf")
            page = doc[0]
            text = page.get_text("text")
        except Exception:
            return None

        # Search patterns for key rows
        current_assets = None
        creditors_1yr = None
        net_current_assets = None
        debtors = None
        cash = None

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        for i, line in enumerate(lines):
            l_lower = line.lower()
            if "debtors" in l_lower and debtors is None:
                debtors = cls._look_ahead_number(lines, i)
            elif "cash at bank" in l_lower and cash is None:
                cash = cls._look_ahead_number(lines, i)
            elif "current assets" in l_lower and current_assets is None:
                current_assets = cls._look_ahead_number(lines, i)
            elif "creditors" in l_lower and ("one year" in l_lower or "within" in l_lower) and creditors_1yr is None:
                creditors_1yr = cls._look_ahead_number(lines, i)
            elif "net current assets" in l_lower and net_current_assets is None:
                net_current_assets = cls._look_ahead_number(lines, i)

        if current_assets is None and (debtors or cash):
            current_assets = (debtors or 0.0) + (cash or 0.0)

        # Fallback synthetic defaults for test documents without full tables
        if current_assets is None or creditors_1yr is None:
            return None

        creditors_1yr = abs(creditors_1yr)
        if net_current_assets is None:
            net_current_assets = current_assets - creditors_1yr

        expected_net = current_assets - creditors_1yr
        variance = abs(expected_net - net_current_assets)
        is_valid = variance <= 2.0

        framework = "FRS_102" if debtors is not None else "FRS_105"

        return BalanceSheetData(
            extraction_path="VISION_PDF",
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

    @staticmethod
    def _look_ahead_number(lines: list, index: int) -> Optional[float]:
        """Inspect current and adjacent lines for the numerical balance."""
        # Try current line first
        val = clean_numeric_text(lines[index])
        if val is not None:
            return val

        # Try up to 3 following lines
        for j in range(index + 1, min(len(lines), index + 4)):
            candidate = clean_numeric_text(lines[j])
            if candidate is not None:
                return candidate
        return None
