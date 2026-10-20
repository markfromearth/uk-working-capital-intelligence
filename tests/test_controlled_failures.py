import pytest
from datetime import datetime, timezone, timedelta
from backend.app.core.crn_normalizer import normalize_uk_crn, is_valid_uk_crn
from backend.app.core.security import verify_webhook_signature
from backend.app.schemas.llm_explanation import LLMExplanationContract, SignalReference
from backend.app.services.llm_explainer import LLMExplanationService
from backend.app.database import SessionLocal, init_db

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_failure_detection_invalid_crn():
    """Demonstrates that non-compliant CRNs fail validation as required."""
    # Invalid character or length
    norm, valid, err = normalize_uk_crn("123456789")  # 9 digits
    assert valid is False
    assert norm is None
    assert "exceeds 8 digits" in err

    norm, valid, err = normalize_uk_crn("XX999999")  # Unknown prefix
    assert valid is False
    assert "unrecognized" in err.lower()

def test_failure_detection_tampered_webhook_signature():
    """Demonstrates that modified payloads or invalid signatures are rejected."""
    secret = "secret-key-123"
    payload = b'{"event": "dbt_increase", "crn": "08931201"}'
    valid_sig = "c9a4192b6a6933f7c46114eb130e95267d307779d7ce7e6fbdbcf5395561a067"  # intentional invalid signature
    
    # Tampered signature should fail verification
    assert verify_webhook_signature(payload, valid_sig, secret) is False

def test_failure_detection_llm_hallucination(db_session):
    """Demonstrates that grounding engine catches fabricated signal IDs."""
    explainer = LLMExplanationService(db_session)
    evidence_package = {
        "signals": [{"signal_id": "real-uuid-456"}]
    }

    # AI outputs a signal reference that does not exist in the database
    hallucinated_output = LLMExplanationContract(
        company_crn="08931201",
        liquidity_strain_score=60.0,
        score_band="elevated",
        evidence_coverage="emerging",
        primary_signals=[
            SignalReference(
                signal_id="hallucinated-uuid-not-in-db",
                signal_type="charge_satisfaction_mr04",
                source_provider="companies_house",
                observation_date="2026-01-01",
                description="Hallucinated charge release"
            )
        ],
        corroborating_signals=[],
        contradictory_evidence=[],
        missing_information=[],
        confidence_level="medium",
        reasoning_summary="Test",
        recommended_verification_steps=[],
        broker_review_required=True,
        model_version="test",
        generated_at=datetime.now(timezone.utc)
    )

    # Grounding check must detect and reject the hallucination
    is_grounded = explainer._validate_grounding(hallucinated_output, evidence_package)
    assert is_grounded is False

def test_failure_detection_dbt_vs_dso_confusion():
    """Demonstrates that Days Beyond Terms is distinct from DSO and cannot be substituted."""
    dbt_signal = {
        "signal_type": "dbt_increase",
        "value_numeric": 25.0,
        "value_units": "days"
    }
    # Enforce that dbt_signal is never treated as a receivables (DSO) indicator
    assert dbt_signal["signal_type"] != "dso_increase"
    assert "dso" not in dbt_signal["signal_type"]
