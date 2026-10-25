"""
The Gazette (Official Public Record) Linked Data & Notices Adapter.
Ingests statutory corporate insolvency, winding-up petitions, and administration
notices under Open Government Licence / Crown Copyright (100% Free).
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger
from backend.app.core.ssrf_protection import SafeHttpClient
from backend.app.providers.base import (
    AdapterHealthStatus,
    IProviderAdapter,
    ProviderStatus,
)

THE_GAZETTE_NOTICES_BASE = "https://www.thegazette.co.uk/all-notices"


class GazetteAdapter(IProviderAdapter):
    """
    Adapter for The Gazette statutory public notices.
    Captures winding-up petitions, appointments of liquidators/administrators,
    and creditors' voluntary liquidations directly from the official gazette.
    """

    def __init__(self):
        self._test_fixtures: Dict[str, List[Dict[str, Any]]] = {}
        self.last_fetch_status: str = "IDLE"
        self.last_error: Optional[str] = None

    @property
    def provider_name(self) -> str:
        return "the_gazette"

    def register_fixture(self, crn: str, notices: List[Dict[str, Any]]):
        """Register deterministic test fixture for hermetic testing."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        key = norm_crn if valid and norm_crn else crn
        self._test_fixtures[key] = notices

    def get_health_status(self) -> ProviderStatus:
        if self.last_fetch_status == "TIMED_OUT":
            return ProviderStatus(
                provider_name=self.provider_name,
                status=AdapterHealthStatus.DEGRADED,
                endpoint=THE_GAZETTE_NOTICES_BASE,
                rate_limit_remaining=None,
                message=f"The Gazette request timed out (TIMED_OUT): {self.last_error}",
            )
        return ProviderStatus(
            provider_name=self.provider_name,
            status=AdapterHealthStatus.LIVE,
            endpoint=THE_GAZETTE_NOTICES_BASE,
            rate_limit_remaining=120,
            message="The Gazette Open Public Record active (Crown Copyright / OGL)",
        )

    async def search_insolvency_live(self, company_name_or_crn: str) -> List[Dict[str, Any]]:
        """Live search of The Gazette for corporate insolvency notices via SSRF-hardened SafeHttpClient."""
        import httpx
        url = f"{THE_GAZETTE_NOTICES_BASE}/data.json"
        params = {
            "text": company_name_or_crn,
            "categorycode": "G205000000",  # Corporate Insolvency category
            "results-page-size": 5,
        }
        try:
            client = SafeHttpClient(timeout_seconds=10.0)
            resp = await client.get_async(url, params=params, headers={"Accept": "application/json"})
            self.last_fetch_status = "OK"
            self.last_error = None
            if resp.status_code == 200:
                data = resp.json()
                return data.get("entry", []) or []
            logger.warning(f"The Gazette returned status {resp.status_code}")
            return []
        except (httpx.TimeoutException, TimeoutError) as e:
            self.last_fetch_status = "TIMED_OUT"
            self.last_error = str(e)
            logger.warning(f"The Gazette live lookup timed out: {e}")
            return []
        except Exception as e:
            self.last_fetch_status = "ERROR"
            self.last_error = str(e)
            logger.warning(f"The Gazette live lookup failed: {e}")
            return []

    def fetch_company_signals(self, raw_crn: str) -> List[Dict[str, Any]]:
        """
        Fetch statutory insolvency signals for a UK company CRN.
        Returns normalized signal payloads with explicit provenance metadata (FIXTURE vs LIVE).
        """
        norm_crn, valid, _ = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            return []

        notices = self._test_fixtures.get(norm_crn, [])
        signals: List[Dict[str, Any]] = []

        for notice in notices:
            notice_id = str(notice.get("notice_id", f"GZ_{norm_crn}_{len(signals)+1}"))
            notice_type = str(notice.get("notice_type", "Corporate Insolvency Notice"))
            title = str(notice.get("title", f"Notice in The Gazette ({notice_type})"))
            date_str = notice.get("publication_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            try:
                obs_dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                obs_dt = datetime.now(timezone.utc)

            uri = str(notice.get("uri", f"https://www.thegazette.co.uk/notice/{notice_id}"))
            is_live_notice = bool(notice.get("_live", False))
            v_status = notice.get("verification_status", "verified" if is_live_notice else "fixture")
            prov_mode = "LIVE" if is_live_notice else "FIXTURE"

            sig = {
                "crn": norm_crn,
                "signal_type": "gazette_insolvency_notice",
                "source_provider": self.provider_name,
                "original_event_ref": notice_id,
                "observation_timestamp": obs_dt,
                "value_numeric": 1.0,
                "value_text": f"{notice_type}: {title}",
                "value_units": "notice_count",
                "direction": "strain_increasing",  # Critical insolvency indicator
                "confidence_score": 1.0,  # Official statutory gazette
                "evidence_reference": uri,
                "decay_half_life_days": 365,
                "verification_status": v_status,
                "data_use_restrictions": "crown_copyright_ogl_v3",
                "provenance_details": json.dumps({
                    "source": "the_gazette_notice",
                    "notice_id": notice_id,
                    "mode": prov_mode,
                    "license_classification": "OGL_v3.0_CROWN_COPYRIGHT",
                    "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                }),
                "metadata_json": json.dumps({
                    "notice_code": notice.get("notice_code", ""),
                    "category": "Corporate Insolvency",
                    "provenance_mode": prov_mode,
                }),
            }
            signals.append(sig)

        return signals
