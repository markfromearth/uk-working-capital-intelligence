"""
Accounts Fetcher with S3 Redirect Header-Stripping for Companies House Document API.
Downloads statutory account filings (XHTML or PDF), preventing HTTP 400 Amazon S3 errors.
"""

from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.app.config import settings
from backend.app.core.logging import logger


class AccountsFetcher:
    """Acquires statutory accounts documents with explicit cross-domain redirect security."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.COMPANIES_HOUSE_API_KEY
        self._is_live = bool(self.api_key and self.api_key.strip() and self.api_key != "mock_key")
        self._simulated_docs: Dict[str, Tuple[bytes, str]] = {}

    def register_fixture(self, document_id: str, content: bytes, content_type: str):
        """Register custom fixture for testing."""
        self._simulated_docs[document_id] = (content, content_type)

    def extract_latest_accounts_metadata(
        self, filings: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Find the most recent accounts filing with document metadata link."""
        for f in filings:
            cat = str(f.get("category", "")).lower()
            f_type = str(f.get("type", "")).upper()
            if cat == "accounts" or f_type in ("AA", "AAM", "TOTAL EXEMPTION FULL"):
                links = f.get("links", {})
                if "document_metadata" in links:
                    return f
        return None

    async def download_document(
        self, document_metadata_url: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Download document content from Document API.
        CRITICAL ARCHITECTURAL REQUIREMENT:
        The Document API returns an HTTP 302/307 redirect to Amazon S3.
        When following the redirect, the Basic Authorization header MUST be stripped.
        """
        doc_id = document_metadata_url.rstrip("/").split("/")[-1]
        if doc_id in self._simulated_docs:
            return self._simulated_docs[doc_id]

        if not self._is_live:
            # Fall back to default simulated XHTML
            sample_xhtml = self._generate_simulated_ixbrl()
            return sample_xhtml, "application/xhtml+xml"

        content_url = f"{document_metadata_url}/content"
        auth = httpx.BasicAuth(self.api_key, "")
        headers = {"Accept": "application/xhtml+xml, application/pdf"}

        # Step 1: Request with follow_redirects=False to inspect redirect target
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(content_url, auth=auth, headers=headers, follow_redirects=False)

            if resp.status_code in (301, 302, 303, 307):
                redirect_target = resp.headers.get("Location")
                if not redirect_target:
                    logger.error("CH Document API returned redirect status without Location header")
                    return None, None

                # Step 2: Request redirect target WITHOUT the Authorization header
                s3_resp = await client.get(redirect_target, follow_redirects=True)
                if s3_resp.status_code == 200:
                    c_type = s3_resp.headers.get("Content-Type", "application/pdf")
                    return s3_resp.content, c_type
                else:
                    logger.error(f"S3 download failed with status {s3_resp.status_code}")
                    return None, None

            elif resp.status_code == 200:
                c_type = resp.headers.get("Content-Type", "application/pdf")
                return resp.content, c_type
            else:
                logger.error(f"Failed to fetch document metadata: status {resp.status_code}")
                return None, None

    def _generate_simulated_ixbrl(self) -> bytes:
        """Sample synthetic UK micro-entity iXBRL content."""
        html = """<?xml version="1.0" encoding="UTF-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2008/inlineXBRL" xmlns:uk-gaap="http://www.xbrl.org/uk/gaap/core/2009-09-01">
        <body>
            <h1>BALANCE SHEET AS AT 31 DECEMBER 2025</h1>
            <table>
                <tr>
                    <td>Current assets</td>
                    <td><ix:nonFraction name="uk-gaap:Debtors" unitRef="GBP" decimals="0">450000</ix:nonFraction></td>
                </tr>
                <tr>
                    <td>Cash at bank</td>
                    <td><ix:nonFraction name="uk-gaap:CashBankInHand" unitRef="GBP" decimals="0">35000</ix:nonFraction></td>
                </tr>
                <tr>
                    <td>Total current assets</td>
                    <td><ix:nonFraction name="uk-gaap:CurrentAssets" unitRef="GBP" decimals="0">485000</ix:nonFraction></td>
                </tr>
                <tr>
                    <td>Creditors: amounts falling due within one year</td>
                    <td><ix:nonFraction name="uk-gaap:CreditorsDueWithinOneYear" unitRef="GBP" decimals="0">(260000)</ix:nonFraction></td>
                </tr>
                <tr>
                    <td>Net current assets</td>
                    <td><ix:nonFraction name="uk-gaap:NetCurrentAssetsLiabilities" unitRef="GBP" decimals="0">225000</ix:nonFraction></td>
                </tr>
            </table>
        </body>
        </html>"""
        return html.encode("utf-8")
