"""
Accounts Fetcher with S3 Redirect Header-Stripping for Companies House Document API.
Downloads statutory account filings (XHTML or PDF), preventing HTTP 400 Amazon S3 errors.
Zero simulated data generation: only returns genuine retrieved statutory documents.
"""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin
import httpx

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.core.ssrf_protection import validate_safe_url


class AccountsFetcher:
    """Acquires statutory accounts documents with explicit cross-domain redirect security."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.COMPANIES_HOUSE_API_KEY
        self._is_live = bool(self.api_key and self.api_key.strip() and self.api_key != "mock_key")
        self._test_fixtures: Dict[str, Tuple[bytes, str]] = {}

    def register_fixture(self, document_id: str, content: bytes, content_type: str):
        """Register custom fixture for testing."""
        self._test_fixtures[document_id] = (content, content_type)

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
        if doc_id in self._test_fixtures:
            return self._test_fixtures[doc_id]

        if not self._is_live:
            return None, None

        content_url = f"{document_metadata_url.rstrip('/')}/content"
        is_safe, reason = validate_safe_url(content_url, resolve_dns=True, allow_http=False)
        if not is_safe:
            logger.error(f"SSRF boundary blocked document_metadata_url '{content_url}': {reason}")
            return None, None

        auth = httpx.BasicAuth(self.api_key, "")
        headers = {"Accept": "application/xhtml+xml, application/pdf"}

        # Step 1: Request with follow_redirects=False to inspect redirect target
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=False) as client:
                resp = await client.get(content_url, auth=auth, headers=headers)

                if resp.status_code in (301, 302, 303, 307, 308):
                    redirect_target = resp.headers.get("Location")
                    if not redirect_target:
                        logger.error("CH Document API returned redirect status without Location header")
                        return None, None

                    resolved_redirect = urljoin(content_url, redirect_target)
                    redir_safe, redir_reason = validate_safe_url(resolved_redirect, resolve_dns=True, allow_http=False)
                    if not redir_safe:
                        logger.error(f"SSRF boundary blocked S3 redirect target '{resolved_redirect}': {redir_reason}")
                        return None, None

                    # Step 2: Request redirect target WITHOUT the Authorization header
                    s3_resp = await client.get(resolved_redirect)
                    if s3_resp.status_code == 200:
                        if len(s3_resp.content) > 15 * 1024 * 1024:
                            logger.error("S3 document exceeds 15 MB size limit")
                            return None, None
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
        except Exception as e:
            logger.warning(f"Error downloading document from {document_metadata_url}: {e}")
            return None, None
