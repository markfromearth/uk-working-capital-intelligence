import hashlib
import json
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import httpx

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.providers.base import IProviderAdapter, IStreamingAdapter, ProviderStatus, AdapterHealthStatus

COMPANIES_HOUSE_API_URL = "https://api.company-information.service.gov.uk"
COMPANIES_HOUSE_STREAM_URL = "https://stream.companieshouse.gov.uk"

class CompaniesHouseAdapter(IProviderAdapter, IStreamingAdapter):
    """
    Official Companies House Provider Adapter.
    Adheres to official Companies House REST API & Charges Stream specification.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = settings.COMPANIES_HOUSE_API_KEY if api_key is None else api_key
        self._is_live = bool(self.api_key and self.api_key.strip() and self.api_key != "mock_key")

    @property
    def provider_name(self) -> str:
        return "companies_house"

    def get_health_status(self) -> ProviderStatus:
        if self._is_live:
            return ProviderStatus(
                provider_name=self.provider_name,
                status=AdapterHealthStatus.LIVE,
                endpoint=COMPANIES_HOUSE_API_URL,
                rate_limit_remaining=600,
                last_event_timestamp=datetime.now(timezone.utc),
                message="Configured with genuine Companies House API credentials."
            )
        return ProviderStatus(
            provider_name=self.provider_name,
            status=AdapterHealthStatus.SIMULATED if settings.DEMO_MODE else AdapterHealthStatus.UNCONFIGURED,
            endpoint=COMPANIES_HOUSE_API_URL,
            rate_limit_remaining=None,
            last_event_timestamp=datetime.now(timezone.utc),
            message="No live API key present; operating in verified deterministic simulation mode." if settings.DEMO_MODE else "No live Companies House API key configured."
        )

    def fetch_company_profile(self, raw_crn: str) -> Optional[Dict[str, Any]]:
        """Fetch registered company profile from official API or deterministic fixture."""
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            logger.warning(f"Invalid CRN for Companies House lookup: {raw_crn} - {err}")
            return None

        if self._is_live:
            return self._fetch_live_profile(norm_crn)
        if settings.DEMO_MODE:
            return self._generate_simulated_profile(norm_crn)
        logger.warning(f"Companies House API credentials unconfigured and DEMO_MODE=False. Cannot fetch profile for {norm_crn}.")
        return None

    def fetch_company_charges(self, raw_crn: str) -> List[Dict[str, Any]]:
        """Fetch charges (MR01 registrations, MR04 satisfactions) for a company."""
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            return []

        if self._is_live:
            return self._fetch_live_charges(norm_crn)
        if settings.DEMO_MODE:
            return self._generate_simulated_charges(norm_crn)
        logger.warning(f"Companies House API credentials unconfigured and DEMO_MODE=False. Cannot fetch charges for {norm_crn}.")
        return []

    def fetch_company_officers(self, raw_crn: str) -> List[Dict[str, Any]]:
        """Fetch official statutory officers (directors, secretaries, LLP members) for a company."""
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            return []
        if self._is_live:
            return self._fetch_live_officers(norm_crn)
        if settings.DEMO_MODE:
            return self._generate_simulated_officers(norm_crn)
        logger.warning(f"Companies House API credentials unconfigured and DEMO_MODE=False. Cannot fetch officers for {norm_crn}.")
        return []

    def fetch_company_psc(self, raw_crn: str) -> List[Dict[str, Any]]:
        """Fetch Persons with Significant Control (PSC) register for a company."""
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            return []
        if self._is_live:
            return self._fetch_live_psc(norm_crn)
        if settings.DEMO_MODE:
            return self._generate_simulated_psc(norm_crn)
        logger.warning(f"Companies House API credentials unconfigured and DEMO_MODE=False. Cannot fetch PSC for {norm_crn}.")
        return []

    def fetch_company_signals(self, crn: str) -> List[Dict[str, Any]]:
        """Normalises charge filings into structured signal payloads."""
        charges = self.fetch_company_charges(crn)
        signals = []
        for chg in charges:
            signals.extend(self._parse_charge_into_signals(crn, chg))
        return signals

    def poll_stream_events(self, timepoint: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """
        Poll charge events starting from cursor `timepoint`.
        In live mode, queries the Companies House charges stream.
        In simulation mode, generates deterministic incremental events with a monotonic cursor.
        """
        if self._is_live:
            return self._poll_live_stream(timepoint, limit)
        if settings.DEMO_MODE:
            return self._poll_simulated_stream(timepoint, limit)
        logger.warning("Companies House streaming credentials unconfigured and DEMO_MODE=False.")
        return {"events": [], "next_timepoint": timepoint, "has_more": False, "error": "unconfigured"}

    def _fetch_live_profile(self, crn: str) -> Optional[Dict[str, Any]]:
        url = f"{COMPANIES_HOUSE_API_URL}/company/{crn}"
        for attempt in range(settings.MAX_RETRY_ATTEMPTS):
            try:
                # Companies House HTTP Basic Auth: username is API key, password is empty string
                response = httpx.get(url, auth=(self.api_key, ""), timeout=10.0)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(retry_after + random.uniform(0.1, 0.5))
                else:
                    response.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Companies House API error (attempt {attempt + 1}): {e}")
                if attempt == settings.MAX_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep((2 ** attempt) + random.uniform(0.1, 0.5))
        return None

    def _fetch_live_charges(self, crn: str) -> List[Dict[str, Any]]:
        url = f"{COMPANIES_HOUSE_API_URL}/company/{crn}/charges"
        try:
            response = httpx.get(url, auth=(self.api_key, ""), timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                return data.get("items", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch live charges for {crn}: {e}")
            return []

    def _fetch_live_officers(self, crn: str) -> List[Dict[str, Any]]:
        url = f"{COMPANIES_HOUSE_API_URL}/company/{crn}/officers"
        try:
            response = httpx.get(url, auth=(self.api_key, ""), timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                return data.get("items", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch live officers for {crn}: {e}")
            return []

    def _fetch_live_psc(self, crn: str) -> List[Dict[str, Any]]:
        url = f"{COMPANIES_HOUSE_API_URL}/company/{crn}/persons-with-significant-control"
        try:
            response = httpx.get(url, auth=(self.api_key, ""), timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                return data.get("items", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch live PSC for {crn}: {e}")
            return []

    def _poll_live_stream(self, timepoint: Optional[str], limit: int) -> Dict[str, Any]:
        url = f"{COMPANIES_HOUSE_STREAM_URL}/charges"
        params = {}
        if timepoint:
            params["timepoint"] = timepoint
        try:
            # Note: Streaming endpoint in live mode provides newline-delimited JSON
            with httpx.stream("GET", url, auth=(self.api_key, ""), params=params, timeout=30.0) as stream:
                if stream.status_code == 416:
                    logger.warning(f"Companies House streaming cursor {timepoint} expired (HTTP 416). Resetting to stream head with degraded gap status.")
                    return {"events": [], "next_timepoint": None, "has_more": False, "error": "cursor_expired_reset", "gap_detected": True, "gap_from": timepoint}

                events = []
                last_tp = timepoint
                for line in stream.iter_lines():
                    if line:
                        evt = json.loads(line)
                        events.append(evt)
                        last_tp = str(evt.get("timepoint", last_tp))
                        if len(events) >= limit:
                            break
                return {"events": events, "next_timepoint": last_tp, "has_more": len(events) >= limit}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 416:
                logger.warning(f"Companies House stream cursor {timepoint} out of range (HTTP 416). Resetting with degraded gap.")
                return {"events": [], "next_timepoint": None, "has_more": False, "error": "cursor_expired_reset", "gap_detected": True, "gap_from": timepoint}
            logger.error(f"Companies House streaming HTTP error: {e}")
            return {"events": [], "next_timepoint": timepoint, "has_more": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error reading Companies House charge stream: {e}")
            return {"events": [], "next_timepoint": timepoint, "has_more": False, "error": str(e)}

    # --- Deterministic Simulation Fixtures for Testing & Demo ---

    def _generate_simulated_profile(self, crn: str) -> Dict[str, Any]:
        """Deterministic profile derived from hash of CRN."""
        h = int(hashlib.md5(crn.encode()).hexdigest(), 16)
        names = ["Apex Logistics UK Ltd", "Vanguard Manufacturing Ltd", "Solent Precision Engineering Ltd", 
                 "Harbour Commercial Services Ltd", "Caledonian Distribution Ltd", "Meridian Retail Solutions Ltd"]
        name = names[h % len(names)]
        return {
            "company_number": crn,
            "company_name": name,
            "company_status": "active",
            "type": "ltd",
            "date_of_creation": "2016-04-12",
            "registered_office_address": {
                "address_line_1": "100 Bishopsgate",
                "locality": "London",
                "postal_code": "EC2N 4AG",
                "country": "United Kingdom"
            },
            "sic_codes": ["49410", "70229"],
            "jurisdiction": "england-wales"
        }

    def _generate_simulated_charges(self, crn: str) -> List[Dict[str, Any]]:
        """
        Generates deterministic charge filings including satisfied charges (MR04)
        to test liquidity score modeling.
        """
        now = datetime.now(timezone.utc)
        h = int(hashlib.md5(crn.encode()).hexdigest(), 16)
        
        # Pattern varies by hash: some have recent MR04 satisfaction, some have partial, some have none
        pattern = h % 3
        charges = []

        if pattern in (0, 1):
            # Satisfied charge (Form MR04)
            satisfaction_date = (now - timedelta(days=15 if pattern == 0 else 45)).strftime("%Y-%m-%d")
            charges.append({
                "id": f"chg-{crn}-001",
                "charge_number": 1,
                "status": "fully-satisfied" if pattern == 0 else "partially-satisfied",
                "created_on": "2021-03-10",
                "satisfied_on": satisfaction_date,
                "classification": {
                    "type": "charge-description",
                    "description": "Debenture including Fixed and Floating Charge over all assets"
                },
                "persons_entitled": [{"name": "Barclays Bank PLC"}],
                "particulars": {"description": "All present and future freehold and leasehold property and book debts."},
                "links": {"self": f"/company/{crn}/charges/chg-{crn}-001"},
                "_simulated": True,
            })

        if pattern == 0:
            # Another historic charge
            charges.append({
                "id": f"chg-{crn}-002",
                "charge_number": 2,
                "status": "outstanding",
                "created_on": "2023-01-15",
                "classification": {
                    "type": "charge-description",
                    "description": "Mortgage debenture"
                },
                "persons_entitled": [{"name": "HSBC UK Bank PLC"}],
                "links": {"self": f"/company/{crn}/charges/chg-{crn}-002"},
                "_simulated": True,
            })

        return charges

    def _generate_simulated_officers(self, crn: str) -> List[Dict[str, Any]]:
        """Deterministic officer appointments including active directors, secretaries, and historical/resigned officers."""
        officer_names = [
            ("Dr. Robert Sterling", "director", "Managing Director", 1974, 5),
            ("Sarah Jenkins", "director", "Finance Director", 1982, 9),
            ("David McAllister", "secretary", "Company Secretary", 1970, 2),
            ("Marcus Vance", "director", "Former Operations Director", 1968, 11)
        ]

        officers = [
            {
                "name": officer_names[0][0],
                "officer_role": "director",
                "occupation": officer_names[0][2],
                "nationality": "British",
                "date_of_birth": {"year": officer_names[0][3], "month": officer_names[0][4]},
                "appointed_on": "2018-03-12",
                "resigned_on": None,
                "links": {"officer": {"appointments": f"/officers/{hashlib.md5(officer_names[0][0].encode()).hexdigest()[:12]}/appointments"}}
            },
            {
                "name": officer_names[1][0],
                "officer_role": "director",
                "occupation": officer_names[1][2],
                "nationality": "British",
                "date_of_birth": {"year": officer_names[1][3], "month": officer_names[1][4]},
                "appointed_on": "2020-07-01",
                "resigned_on": None,
                "links": {"officer": {"appointments": f"/officers/{hashlib.md5(officer_names[1][0].encode()).hexdigest()[:12]}/appointments"}}
            },
            {
                "name": officer_names[2][0],
                "officer_role": "secretary",
                "occupation": officer_names[2][2],
                "nationality": "British",
                "date_of_birth": {"year": officer_names[2][3], "month": officer_names[2][4]},
                "appointed_on": "2019-01-15",
                "resigned_on": None,
                "links": {"officer": {"appointments": f"/officers/{hashlib.md5(officer_names[2][0].encode()).hexdigest()[:12]}/appointments"}}
            },
            {
                "name": officer_names[3][0],
                "officer_role": "director",
                "occupation": officer_names[3][2],
                "nationality": "British",
                "date_of_birth": {"year": officer_names[3][3], "month": officer_names[3][4]},
                "appointed_on": "2015-06-01",
                "resigned_on": "2024-04-15",
                "links": {"officer": {"appointments": f"/officers/{hashlib.md5(officer_names[3][0].encode()).hexdigest()[:12]}/appointments"}}
            }
        ]
        return officers

    def _generate_simulated_psc(self, crn: str) -> List[Dict[str, Any]]:
        """Deterministic Persons with Significant Control (PSC) entries."""
        return [
            {
                "name": "Dr. Robert Sterling",
                "kind": "individual-person-with-significant-control",
                "notified_on": "2018-03-12",
                "ceased_on": None,
                "nationality": "British",
                "date_of_birth": {"year": 1974, "month": 5},
                "natures_of_control": [
                    "ownership-of-shares-50-to-75-percent",
                    "voting-rights-50-to-75-percent",
                    "right-to-appoint-and-remove-directors"
                ],
                "links": {"self": f"/company/{crn}/persons-with-significant-control/psc-01"}
            }
        ]

    def _poll_simulated_stream(self, timepoint: Optional[str], limit: int) -> Dict[str, Any]:
        """Provides simulated monotonic stream events for testing durable checkpoint recovery."""
        start_cursor = int(timepoint or "1000")
        events = []
        sample_crns = ["08931201", "SC123456", "09441233", "11234567", "04321987"]
        
        for i in range(min(limit, 5)):
            curr_tp = start_cursor + i + 1
            crn = sample_crns[(curr_tp) % len(sample_crns)]
            events.append({
                "timepoint": curr_tp,
                "resource_kind": "company-charges",
                "resource_uri": f"/company/{crn}/charges/chg-{crn}-stream-{curr_tp}",
                "event": {
                    "timepoint": curr_tp,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "type": "changed",
                    "fields": {
                        "company_number": crn,
                        "status": "fully-satisfied",
                        "form_type": "MR04",
                        "action": "satisfaction-of-charge",
                        "description": "Statement of satisfaction in full of a charge (MR04)"
                    }
                }
            })

        next_cursor = str(start_cursor + len(events))
        return {
            "events": events,
            "next_timepoint": next_cursor,
            "has_more": True
        }

    def _parse_charge_into_signals(self, crn: str, charge: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parses charge record into normalized signal dictionaries with explicit LIVE vs SIMULATED provenance."""
        signals = []
        charge_id = charge.get("id", "unknown")
        status = charge.get("status", "").lower()
        satisfied_on = charge.get("satisfied_on")
        is_sim = bool(charge.get("_simulated", not self._is_live))
        v_status = "simulated" if is_sim else "verified"
        prov_details = json.dumps({
            "mode": "SIMULATED" if is_sim else "LIVE",
            "provider": self.provider_name,
            "license_classification": "OGL_v3.0_SIMULATED" if is_sim else "OGL_v3.0",
            "freshness_status": "CURRENT",
            "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        if status in ("fully-satisfied", "partially-satisfied") and satisfied_on:
            try:
                obs_dt = datetime.strptime(satisfied_on, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                obs_dt = datetime.now(timezone.utc)
                
            is_full = (status == "fully-satisfied")
            signals.append({
                "crn": crn,
                "signal_type": "charge_satisfaction_mr04",
                "source_provider": self.provider_name,
                "original_event_ref": charge_id,
                "observation_timestamp": obs_dt,
                "value_numeric": 1.0 if is_full else 0.5,
                "value_text": f"Charge {status}: {charge.get('classification', {}).get('description', 'Debenture')}",
                "value_units": "scope_weight",
                "direction": "strain_increasing",  # Potential refinancing or facility discharge
                "confidence_score": 0.95 if is_full else 0.80,
                "evidence_reference": f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges/{charge_id}",
                "decay_half_life_days": 90,
                "verification_status": v_status,
                "provenance_details": prov_details,
                "metadata_json": json.dumps({
                    "charge_id": charge_id,
                    "scope": "full" if is_full else "partial",
                    "persons_entitled": [p.get("name") for p in charge.get("persons_entitled", [])],
                    "is_simulated": is_sim,
                })
            })
            
        elif status == "outstanding":
            created_on = charge.get("created_on")
            if created_on:
                try:
                    obs_dt = datetime.strptime(created_on, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    obs_dt = datetime.now(timezone.utc)
                signals.append({
                    "crn": crn,
                    "signal_type": "charge_creation",
                    "source_provider": self.provider_name,
                    "original_event_ref": charge_id,
                    "observation_timestamp": obs_dt,
                    "value_numeric": 1.0,
                    "value_text": f"New charge registered: {charge.get('classification', {}).get('description', 'Security')}",
                    "value_units": "count",
                    "direction": "neutral",
                    "confidence_score": 0.90,
                    "evidence_reference": f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges/{charge_id}",
                    "decay_half_life_days": 180,
                    "verification_status": v_status,
                    "provenance_details": prov_details,
                    "metadata_json": json.dumps({"charge_id": charge_id, "is_simulated": is_sim})
                })
        return signals
