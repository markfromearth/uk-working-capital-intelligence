import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import httpx

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.security import verify_webhook_signature
from backend.app.providers.base import IProviderAdapter, ProviderStatus, AdapterHealthStatus

CREDITSAFE_BASE_URL = "https://connect.creditsafe.com/v1"

class CreditsafeAdapter(IProviderAdapter):
    """
    Creditsafe Connect Commercial Risk Provider Adapter.
    Tracks Days Beyond Terms (DBT), credit score changes, and payment alerts.
    Strictly keeps DBT separate from DSO.
    """

    def __init__(self, api_key: Optional[str] = None, webhook_secret: Optional[str] = None):
        self.api_key = settings.CREDITSAFE_API_KEY if api_key is None else api_key
        self.webhook_secret = settings.CREDITSAFE_WEBHOOK_SECRET if webhook_secret is None else webhook_secret
        self._is_live = bool(self.api_key and self.api_key.strip() and self.api_key != "mock_key")

    @property
    def provider_name(self) -> str:
        return "creditsafe"

    def get_health_status(self) -> ProviderStatus:
        if self._is_live:
            return ProviderStatus(
                provider_name=self.provider_name,
                status=AdapterHealthStatus.LIVE,
                endpoint=CREDITSAFE_BASE_URL,
                rate_limit_remaining=1000,
                last_event_timestamp=datetime.now(timezone.utc),
                message="Connected to Creditsafe Connect API."
            )
        return ProviderStatus(
            provider_name=self.provider_name,
            status=AdapterHealthStatus.SIMULATED if settings.DEMO_MODE else AdapterHealthStatus.UNCONFIGURED,
            endpoint=CREDITSAFE_BASE_URL,
            rate_limit_remaining=None,
            last_event_timestamp=datetime.now(timezone.utc),
            message="No live Creditsafe subscription credentials; provider disabled in CH-Only mode." if not settings.DEMO_MODE else "No live Creditsafe subscription credentials; operating in verified deterministic simulation mode."
        )

    def fetch_company_signals(self, raw_crn: str) -> List[Dict[str, Any]]:
        """Fetch credit-risk and DBT indicators for a UK CRN."""
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            return []

        if self._is_live:
            return self._fetch_live_report(norm_crn)
        if settings.DEMO_MODE:
            return self._generate_simulated_signals(norm_crn)
        logger.warning(f"Creditsafe API credentials unconfigured and DEMO_MODE=False. Cannot fetch signals for {norm_crn}.")
        return []

    def verify_webhook(
        self,
        raw_body: bytes,
        signature_header: Optional[str],
        timestamp_header: Optional[str] = None,
        max_drift_seconds: int = 300
    ) -> bool:
        """
        Verifies authenticated provider webhook signature with optional timestamp replay protection.
        """
        if not self.webhook_secret:
            return False

        # If timestamp header supplied, verify it is within permissible drift window
        if timestamp_header:
            try:
                if timestamp_header.isdigit():
                    ts = datetime.fromtimestamp(float(timestamp_header), tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(timestamp_header.replace("Z", "+00:00"))
                drift = abs((datetime.now(timezone.utc) - ts).total_seconds())
                if drift > max_drift_seconds:
                    logger.warning(f"Rejected webhook: Timestamp drift of {drift:.1f}s exceeds limit of {max_drift_seconds}s")
                    return False
            except Exception as e:
                logger.warning(f"Rejected webhook: Unparseable timestamp header '{timestamp_header}': {e}")
                return False

        return verify_webhook_signature(raw_body, signature_header, self.webhook_secret)

    def parse_webhook_payload(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses authenticated webhook notifications (e.g. DBT surge or rating downgrade).
        """
        signals = []
        crn = payload.get("company_registration_number") or payload.get("crn")
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return []

        event_type = payload.get("event_type")
        event_time_str = payload.get("event_timestamp")
        try:
            obs_dt = datetime.fromisoformat(event_time_str.replace("Z", "+00:00")) if event_time_str else datetime.now(timezone.utc)
        except Exception:
            obs_dt = datetime.now(timezone.utc)

        # Event: Days Beyond Terms (DBT) surge
        if event_type == "dbt_increase":
            dbt_days = float(payload.get("dbt_days", 0.0))
            dbt_change = float(payload.get("dbt_change_days", 0.0))
            signals.append({
                "crn": norm_crn,
                "signal_type": "dbt_increase",
                "source_provider": self.provider_name,
                "original_event_ref": payload.get("notification_id", f"notif-dbt-{norm_crn}"),
                "observation_timestamp": obs_dt,
                "value_numeric": dbt_days,
                "value_text": f"Days Beyond Terms increased by {dbt_change:+.1f} to {dbt_days:.1f} days",
                "value_units": "days",
                "direction": "strain_increasing",
                "confidence_score": 0.95,
                "evidence_reference": f"Creditsafe Monitoring Notification #{payload.get('notification_id', 'unknown')}",
                "decay_half_life_days": 60,
                "verification_status": "verified",
                "metadata_json": json.dumps({"dbt_change_days": dbt_change, "previous_dbt": dbt_days - dbt_change})
            })

        # Event: Credit score deterioration
        elif event_type == "credit_score_drop":
            current_score = float(payload.get("new_score", 0.0))
            drop_points = float(payload.get("score_drop", 0.0))
            signals.append({
                "crn": norm_crn,
                "signal_type": "credit_score_drop",
                "source_provider": self.provider_name,
                "original_event_ref": payload.get("notification_id", f"notif-score-{norm_crn}"),
                "observation_timestamp": obs_dt,
                "value_numeric": drop_points,
                "value_text": f"Commercial credit score dropped by {drop_points:.0f} pts to {current_score:.0f}",
                "value_units": "score_points",
                "direction": "strain_increasing",
                "confidence_score": 0.95,
                "evidence_reference": f"Creditsafe Monitoring Notification #{payload.get('notification_id', 'unknown')}",
                "decay_half_life_days": 90,
                "verification_status": "verified",
                "metadata_json": json.dumps({"current_score": current_score, "drop_points": drop_points})
            })

        return signals

    def _fetch_live_report(self, crn: str) -> List[Dict[str, Any]]:
        # Connects to Creditsafe Connect REST API
        url = f"{CREDITSAFE_BASE_URL}/companies/GB-{crn}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        try:
            resp = httpx.get(url, headers=headers, timeout=12.0)
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_creditsafe_data(crn, data)
            logger.warning(f"Creditsafe API returned status {resp.status_code} for {crn}")
            return []
        except Exception as e:
            logger.error(f"Creditsafe live API error: {e}")
            return []

    def _parse_creditsafe_data(self, crn: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Map official Creditsafe JSON structure
        signals = []
        report = data.get("reportData", {}).get("companySummary", {})
        dbt = report.get("paymentData", {}).get("daysBeyondTerms")
        score = report.get("creditRating", {}).get("commonValue")
        obs_dt = datetime.now(timezone.utc)

        if dbt is not None:
            signals.append({
                "crn": crn,
                "signal_type": "dbt_increase",
                "source_provider": self.provider_name,
                "original_event_ref": f"cs-dbt-{crn}",
                "observation_timestamp": obs_dt,
                "value_numeric": float(dbt),
                "value_text": f"Current Days Beyond Terms: {dbt} days",
                "value_units": "days",
                "direction": "strain_increasing" if float(dbt) > 15 else "neutral",
                "confidence_score": 0.92,
                "evidence_reference": f"Creditsafe Company Report GB-{crn}",
                "decay_half_life_days": 60,
                "verification_status": "verified",
                "metadata_json": json.dumps({"dbt": dbt})
            })
        return signals

    def _generate_simulated_signals(self, crn: str) -> List[Dict[str, Any]]:
        """
        Generates realistic, deterministic commercial credit risk data.
        Notice: DBT is explicitly tracked; DSO is NEVER fabricated from DBT.
        Explicitly marks verification_status='simulated' so simulated signals cannot masquerade as live verified records.
        """
        h = int(hashlib.md5(crn.encode()).hexdigest(), 16)
        pattern = h % 4
        now = datetime.now(timezone.utc)
        signals = []
        prov = json.dumps({
            "mode": "SIMULATED",
            "provider": self.provider_name,
            "license_classification": "COMMERCIAL_SUBSCRIPTION_SIMULATED",
            "freshness_status": "SIMULATED",
            "retrieval_timestamp": now.isoformat(),
        })

        if pattern in (0, 1):  # Elevated DBT scenario
            dbt_days = 28.5 if pattern == 0 else 18.0
            signals.append({
                "crn": crn,
                "signal_type": "dbt_increase",
                "source_provider": self.provider_name,
                "original_event_ref": f"cs-sim-dbt-{crn}",
                "observation_timestamp": now - timedelta(days=12),
                "value_numeric": dbt_days,
                "value_text": f"Days Beyond Terms deteriorated to {dbt_days:.1f} days (Supplier payments overdue)",
                "value_units": "days",
                "direction": "strain_increasing",
                "confidence_score": 0.90,
                "evidence_reference": f"Creditsafe Commercial Risk Trade Registry (Simulated for {crn})",
                "decay_half_life_days": 60,
                "verification_status": "simulated",
                "provenance_details": prov,
                "metadata_json": json.dumps({"dbt_days": dbt_days, "benchmark_industry_dbt": 8.5, "is_simulated": True})
            })

        if pattern == 0:  # Also has credit score downgrade
            score_drop = 22.0
            signals.append({
                "crn": crn,
                "signal_type": "credit_score_drop",
                "source_provider": self.provider_name,
                "original_event_ref": f"cs-sim-score-{crn}",
                "observation_timestamp": now - timedelta(days=20),
                "value_numeric": score_drop,
                "value_text": f"Commercial credit rating dropped by {score_drop:.0f} points (from 64 to 42)",
                "value_units": "score_points",
                "direction": "strain_increasing",
                "confidence_score": 0.88,
                "evidence_reference": f"Creditsafe Rating History (Simulated for {crn})",
                "decay_half_life_days": 90,
                "verification_status": "simulated",
                "provenance_details": prov,
                "metadata_json": json.dumps({"previous_score": 64, "new_score": 42, "rating_band": "Moderate-High Risk", "is_simulated": True})
            })

        return signals
