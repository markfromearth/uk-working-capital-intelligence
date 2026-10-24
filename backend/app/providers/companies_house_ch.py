"""
Companies House API Provider for CH-Only Invoice Finance Warden.
Implements token-bucket rate limiting, canonical CRN normalization,
resilient HTTP client calls, and deterministic simulation fallbacks.
"""

import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn

COMPANIES_HOUSE_REST_BASE = "https://api.company-information.service.gov.uk"
COMPANIES_HOUSE_DOC_BASE = "https://document-api.company-information.service.gov.uk"


class AsyncTokenBucket:
    """Async token bucket to enforce Companies House 600 req / 5 min limit (max ~1.8 req/s)."""

    def __init__(self, capacity: float = 10.0, refill_rate: float = 1.8):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.refill_rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0


class CompaniesHouseCHClient:
    """Production Companies House REST & Document Client."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.COMPANIES_HOUSE_API_KEY
        self._is_live = bool(self.api_key and self.api_key.strip() and self.api_key != "mock_key")
        self.rate_limiter = AsyncTokenBucket()
        self._simulated_store: Dict[str, Dict[str, Any]] = {}

    def register_fixture(self, crn: str, data: Dict[str, Any]):
        """Register custom test fixture data for a CRN."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if valid and norm_crn:
            self._simulated_store[norm_crn] = data

    async def fetch_profile(self, crn: str) -> Optional[Dict[str, Any]]:
        """Fetch canonical company profile."""
        norm_crn, valid, err = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            logger.warning(f"Invalid CRN for CH lookup: {crn} - {err}")
            return None

        if norm_crn in self._simulated_store:
            return self._simulated_store[norm_crn].get("profile")

        if not self._is_live:
            return self._generate_simulated_profile(norm_crn)

        await self.rate_limiter.acquire()
        url = f"{COMPANIES_HOUSE_REST_BASE}/company/{norm_crn}"
        auth = httpx.BasicAuth(self.api_key, "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=auth)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            logger.error(f"CH profile lookup failed for {norm_crn}: status {resp.status_code}")
            return None

    async def fetch_charges(self, crn: str) -> List[Dict[str, Any]]:
        """Fetch mortgage and charge records."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return []

        if norm_crn in self._simulated_store:
            return self._simulated_store[norm_crn].get("charges", [])

        if not self._is_live:
            return self._generate_simulated_charges(norm_crn)

        await self.rate_limiter.acquire()
        url = f"{COMPANIES_HOUSE_REST_BASE}/company/{norm_crn}/charges"
        auth = httpx.BasicAuth(self.api_key, "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=auth)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", [])
            return []

    async def fetch_filing_history(self, crn: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch filing history (accounts, officers, gazette)."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return []

        if norm_crn in self._simulated_store:
            return self._simulated_store[norm_crn].get("filing_history", [])

        if not self._is_live:
            return self._generate_simulated_filings(norm_crn)

        await self.rate_limiter.acquire()
        url = f"{COMPANIES_HOUSE_REST_BASE}/company/{norm_crn}/filing-history"
        params = {"category": category} if category else {}
        auth = httpx.BasicAuth(self.api_key, "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=auth, params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", [])
            return []

    async def fetch_officers(self, crn: str) -> List[Dict[str, Any]]:
        """Fetch company officers."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return []

        if norm_crn in self._simulated_store:
            return self._simulated_store[norm_crn].get("officers", [])

        if not self._is_live:
            return self._generate_simulated_officers(norm_crn)

        await self.rate_limiter.acquire()
        url = f"{COMPANIES_HOUSE_REST_BASE}/company/{norm_crn}/officers"
        auth = httpx.BasicAuth(self.api_key, "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=auth)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", [])
            return []

    # --- Deterministic Simulation Fixtures for Testing & Demo ---
    def _generate_simulated_profile(self, crn: str) -> Dict[str, Any]:
        return {
            "company_number": crn,
            "company_name": f"SIMULATED ENTERPRISE {crn} LTD",
            "company_status": "active",
            "type": "ltd",
            "date_of_creation": "2018-04-12",
            "sic_codes": ["49410"],  # Freight transport by road (B2B prime)
            "accounts": {
                "accounting_reference_date": {"day": "31", "month": "12"},
                "overdue": False,
            },
            "confirmation_statement": {"overdue": False},
        }

    def _generate_simulated_charges(self, crn: str) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        sat_date = (now - timedelta(days=210)).strftime("%Y-%m-%d")
        return [
            {
                "id": f"chg-{crn}-1",
                "charge_code": f"0000000{crn[:1]}001",
                "status": "fully-satisfied",
                "created_on": "2020-01-15",
                "satisfied_on": sat_date,
                "persons_entitled": [{"name": "BARCLAYS BANK PLC"}],
                "particulars": {"type": "contains-floating-charge", "floating_charge_covers_all": True},
            }
        ]

    def _generate_simulated_filings(self, crn: str) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return [
            {
                "category": "accounts",
                "type": "AA",
                "date": (now - timedelta(days=120)).strftime("%Y-%m-%d"),
                "description": "accounts-with-accounts-type-micro-entity",
                "links": {"document_metadata": f"{COMPANIES_HOUSE_DOC_BASE}/document/doc-{crn}-acc"},
            }
        ]

    def _generate_simulated_officers(self, crn: str) -> List[Dict[str, Any]]:
        return [
            {
                "name": "SMITH, John David",
                "officer_role": "director",
                "appointed_on": "2018-04-12",
                "resigned_on": None,
            }
        ]
