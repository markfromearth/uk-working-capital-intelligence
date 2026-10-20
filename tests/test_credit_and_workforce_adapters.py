import json
import pytest
from datetime import datetime, timezone, timedelta
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.workforce import WorkforceAdapter
from backend.app.core.security import verify_webhook_signature

def test_creditsafe_webhook_signature_and_replay_protection():
    secret = "test-webhook-secret-2026"
    adapter = CreditsafeAdapter(webhook_secret=secret)

    payload = {
        "event_type": "dbt_increase",
        "crn": "08931201",
        "dbt_days": 28.5,
        "dbt_change_days": 12.0,
        "notification_id": "notif-999",
        "event_timestamp": datetime.now(timezone.utc).isoformat()
    }
    raw_bytes = json.dumps(payload).encode("utf-8")

    import hmac, hashlib
    valid_sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    # Valid signature
    assert adapter.verify_webhook(raw_bytes, valid_sig) is True
    assert adapter.verify_webhook(raw_bytes, f"sha256={valid_sig}") is True

    # Tampered signature
    assert adapter.verify_webhook(raw_bytes, "invalid_sig_abc123") is False

    # Parse payload
    signals = adapter.parse_webhook_payload(payload)
    assert len(signals) == 1
    assert signals[0]["signal_type"] == "dbt_increase"
    assert signals[0]["value_numeric"] == 28.5
    assert signals[0]["value_units"] == "days"

def test_strict_dbt_vs_dso_separation():
    """Verify that DBT is never classified as DSO or fabricated into DSO."""
    adapter = CreditsafeAdapter()
    signals = adapter._generate_simulated_signals("08931201")
    
    for s in signals:
        assert s["signal_type"] != "dso_increase"
        if s["signal_type"] == "dbt_increase":
            assert "days beyond terms" in s["value_text"].lower()
            assert "dso" not in s["signal_type"]

def test_workforce_headcount_calculations():
    adapter = WorkforceAdapter()
    now = datetime.now(timezone.utc)

    # 4 dated observations showing expansion from 50 to 75 (+50% in 90 days, accelerating recently)
    history = [
        {"date": (now - timedelta(days=180)).isoformat(), "headcount": 45},
        {"date": (now - timedelta(days=90)).isoformat(), "headcount": 50},
        {"date": (now - timedelta(days=30)).isoformat(), "headcount": 58},
        {"date": now.isoformat(), "headcount": 75}
    ]

    signals = adapter._compute_workforce_signals("08931201", history)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["signal_type"] == "headcount_surge"
    assert sig["value_numeric"] == 50.0  # (75 - 50) / 50 * 100%
    assert sig["confidence_score"] >= 0.8
    meta = json.loads(sig["metadata_json"])
    assert meta["current_headcount"] == 75
    assert meta["headcount_90d_ago"] == 50
    assert meta["acceleration_pct"] > 0
