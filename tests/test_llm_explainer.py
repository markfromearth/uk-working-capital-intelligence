import pytest
from datetime import datetime, timezone
from backend.app.database import SessionLocal, init_db
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.score import LiquidityScoreRecord
from backend.app.services.llm_explainer import LLMExplanationService
from backend.app.schemas.llm_explanation import LLMExplanationContract, SignalReference

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_deterministic_explanation_generation_and_contract(db_session):
    explainer = LLMExplanationService(db_session)
    now = datetime.now(timezone.utc)

    score_rec = LiquidityScoreRecord(
        crn="08931201",
        score=78.5,
        score_band="strong",
        evidence_coverage="strong",
        model_version="v1.0.0",
        scored_at=now
    )
    snapshot = CompanyFeatureSnapshot(
        crn="08931201",
        as_of_timestamp=now,
        charges_satisfied_90d=1,
        latest_charge_scope="full",
        age_latest_charge_satisfaction_days=14.0,
        dbt_current_days=32.0,
        headcount_growth_90d_pct=38.0,
        dso_data_available=0,  # Missing DSO
        evidence_coverage="strong"
    )

    explanation = explainer.generate_explanation("08931201", score_rec, snapshot)
    
    # Contract validation
    assert isinstance(explanation, LLMExplanationContract)
    assert explanation.company_crn == "08931201"
    assert explanation.liquidity_strain_score == 78.5
    assert explanation.score_band == "strong"
    assert explanation.broker_review_required is True
    assert len(explanation.recommended_verification_steps) >= 1
    
    # Verify missing information identifies DSO
    assert any("DSO" in m for m in explanation.missing_information)

def test_grounding_validation_rejects_hallucinated_signal_ids(db_session):
    explainer = LLMExplanationService(db_session)
    
    evidence_package = {
        "signals": [
            {"signal_id": "real-uuid-001", "signal_type": "charge_satisfaction_mr04"}
        ]
    }
    
    # Valid explanation referencing real UUID
    valid_explanation = LLMExplanationContract(
        company_crn="08931201",
        liquidity_strain_score=50.0,
        score_band="elevated",
        evidence_coverage="emerging",
        primary_signals=[
            SignalReference(signal_id="real-uuid-001", signal_type="charge_satisfaction_mr04", source_provider="ch", observation_date="2026-09-01", description="MR04")
        ],
        corroborating_signals=[],
        contradictory_evidence=[],
        missing_information=[],
        confidence_level="medium",
        reasoning_summary="Valid summary",
        recommended_verification_steps=[],
        broker_review_required=True,
        model_version="test",
        generated_at=datetime.now(timezone.utc)
    )
    assert explainer._validate_grounding(valid_explanation, evidence_package) is True

    # Hallucinated signal ID
    hallucinated_explanation = valid_explanation.model_copy(deep=True)
    hallucinated_explanation.primary_signals[0].signal_id = "fake-uuid-invented-by-ai"
    assert explainer._validate_grounding(hallucinated_explanation, evidence_package) is False
