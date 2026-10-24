import os
import pytest
from datetime import datetime, timedelta, timezone
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.config import settings
from backend.app.models.user import User
from backend.app.models.company import CompanyRegistry
from backend.app.models.signal import NormalizedSignal
from backend.app.models.investigation import InvestigationDecision, CommercialOpportunityAssessment, CommercialFunnelOutcome
from backend.app.core.security import create_access_token, hash_password
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.tool_registry import approved_tool_registry, ToolDefinition
from backend.app.services.investigation_engine import ValueOfInformationEngine
from backend.app.services.opportunity_assessment import CommercialOpportunityAssessor
from backend.app.services.outcome_learning import OutcomeLearningService
from backend.app.services.ml_engine import MachineLearningOpportunityModel

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_adaptive_environment():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)

    # Ensure admin and broker users exist for RBAC testing
    admin_user = db.query(User).filter(User.email == "admin_adaptive@test.com").first()
    if not admin_user:
        admin_user = User(
            email="admin_adaptive@test.com",
            hashed_password=hash_password("AdminSecure2026!"),
            full_name="Adaptive Admin",
            role="admin",
            is_active=1
        )
        db.add(admin_user)

    broker_user = db.query(User).filter(User.email == "broker_adaptive@test.com").first()
    if not broker_user:
        broker_user = User(
            email="broker_adaptive@test.com",
            hashed_password=hash_password("BrokerSecure2026!"),
            full_name="Adaptive Broker",
            role="broker",
            is_active=1
        )
        db.add(broker_user)

    db.commit()
    db.close()

def get_auth_headers(role="broker", user_id=None):
    if not user_id:
        db = SessionLocal()
        u = db.query(User).filter(User.role == role, User.is_active == 1).first()
        user_id = u.id if u else "unknown_user"
        db.close()
    token = create_access_token(subject=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}

# 1. Approved Tool Registry & Circuit Breakers

def test_approved_tool_registry_listing_and_circuit_breakers():
    response = client.get("/api/v1/adaptive/tools")
    assert response.status_code == 200
    tools = response.json()
    assert len(tools) >= 5

    tool_names = [t["tool_name"] for t in tools]
    assert "companies_house_charges_refresh" in tool_names
    assert "companies_house_profile_lookup" in tool_names
    assert "creditsafe_deep_financials" in tool_names
    assert "experian_delinquency_trace" in tool_names
    assert "workforce_hiring_velocity" in tool_names

    # Check fee schedule and rate limits
    for t in tools:
        assert "cost_gbp" in t
        assert t["cost_gbp"] >= 0.0
        assert "rate_limit_per_min" in t
        assert t["rate_limit_per_min"] >= 1

    # Verify circuit breaker operation
    cb = approved_tool_registry.get_circuit_breaker("experian_delinquency_trace")
    assert cb is not None
    assert cb.can_execute() is True
    
    # Record failures up to threshold
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.can_execute() is False
    assert cb.is_open is True

    # Reset circuit breaker for normal operation
    cb.reset()
    assert cb.can_execute() is True
    assert cb.is_open is False

# 2. EVOI Mathematics & Daily Budget Deferral

def test_evoi_investigation_evaluation_and_budget_constraints():
    db = SessionLocal()
    # Clean prior test decision rows for this CRN to ensure isolation
    db.query(InvestigationDecision).filter(InvestigationDecision.crn == "08931201").delete()
    db.commit()

    engine = ValueOfInformationEngine(db)
    crn = "08931201" # Apex Precision Engineering Ltd

    # Evaluate investigation opportunities with default £5.00 daily budget
    decisions = engine.evaluate_investigation_opportunities(crn=crn, company_daily_budget_gbp=5.00)
    assert len(decisions) >= 1

    # All decisions must be valid enum outcomes: INVESTIGATE, DEFER, STOP, ESCALATE_HUMAN_REVIEW
    valid_decisions = {"INVESTIGATE", "DEFER", "STOP", "ESCALATE_HUMAN_REVIEW"}
    for d in decisions:
        assert d.decision in valid_decisions
        assert d.evoi_value is not None
        assert d.estimated_cost_gbp >= 0.0
        assert len(d.decision_rationale) > 0

    # Test budget constraint: with £0.00 budget, paid tools must DEFER
    zero_budget_decisions = engine.evaluate_investigation_opportunities(crn=crn, company_daily_budget_gbp=0.00)
    for d in zero_budget_decisions:
        if d.estimated_cost_gbp > 0.0:
            assert d.decision == "DEFER"
            assert "exceeds remaining" in d.decision_rationale

    # Test recent signal suppression: if signals were refreshed within 24h, EVOI is zero and decision is STOP
    recent_sig = NormalizedSignal(
        crn=crn,
        source_provider="companies_house",
        signal_type="charge_registered",
        direction="strain_increasing",
        confidence_score=1.0,
        observation_timestamp=datetime.now(timezone.utc),
        metadata_json='{"charge_code": "RECENT_TEST_CHARGE"}',
        data_use_restrictions="direct_commercial_prospecting_only"
    )
    db.add(recent_sig)
    db.commit()

    ch_decision = engine.evaluate_single_tool(crn, "companies_house_charges_refresh", remaining_budget=10.0)
    assert ch_decision.decision == "STOP"
    assert "recently refreshed" in ch_decision.decision_rationale
    assert ch_decision.evoi_value == 0.0

    db.close()

# 3. Approved Tool Execution & Signal Normalisation

def test_execute_approved_tool_via_api(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    headers = get_auth_headers(role="admin", user_id=admin.id)
    db.close()
    crn = "08931201"

    # Execute workforce hiring velocity tool
    payload = {
        "crn": crn,
        "tool_name": "workforce_hiring_velocity",
        "actor_notes": "Automated verification test"
    }
    response = client.post("/api/v1/adaptive/investigate/execute", json=payload, headers=headers)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert res_data["tool_name"] == "workforce_hiring_velocity"
    assert res_data["actual_cost_gbp"] == 0.10

    # Verify signals generated contain provenance and data use restrictions
    fresh_db = SessionLocal()
    signal = fresh_db.query(NormalizedSignal).filter(
        NormalizedSignal.crn == crn,
        NormalizedSignal.source_provider == "workforce_intelligence"
    ).order_by(NormalizedSignal.ingestion_timestamp.desc()).first()
    
    assert signal is not None
    assert "workforce" in signal.data_use_restrictions

    # Verify investigation history endpoint
    hist_resp = client.get(f"/api/v1/adaptive/investigations/{crn}")
    assert hist_resp.status_code == 200
    hist = hist_resp.json()
    assert len(hist) >= 1

    fresh_db.close()

# 4. Commercial Opportunity Assessment & Product Fit

def test_commercial_opportunity_assessment():
    db = SessionLocal()
    crn = "08931201" # Apex Precision Engineering Ltd (high DBT, charge releases, workforce pressure)
    assessor = CommercialOpportunityAssessor(db)
    assessment = assessor.assess_company(crn)

    # Validate probability bounds
    assert 0.0 <= assessment.p_qualified_opportunity <= 1.0
    assert 0.0 <= assessment.p_conversion_funded <= 1.0
    assert assessment.estimated_commercial_contribution_gbp > 0.0

    # Product suitability metrics
    assert 0.0 <= assessment.invoice_finance_fit <= 100.0
    assert 0.0 <= assessment.asset_refinance_fit <= 100.0
    assert 0.0 <= assessment.term_loan_fit <= 100.0

    # For Apex Precision with high DBT (24 days late), Invoice Finance should be a strong fit
    assert assessment.invoice_finance_fit >= 50.0

    # Verify API endpoint
    resp = client.get(f"/api/v1/adaptive/assessments/{crn}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["crn"] == crn
    assert "p_qualified_opportunity" in data
    assert "p_conversion_funded" in data
    assert "product_fit" in data
    assert "invoice_finance" in data["product_fit"]
    assert "asset_refinance" in data["product_fit"]
    assert "term_loan" in data["product_fit"]

    db.close()

# 5. Funnel Outcome Recording & Conversion Metrics

def test_funnel_outcome_recording_and_kpis():
    db = SessionLocal()
    broker = db.query(User).filter(User.role == "broker").first()
    headers = get_auth_headers(role="broker", user_id=broker.id)
    crn = "08931201"

    # Record funnel progression
    outcomes_to_test = [
        {"stage": "QUALIFIED_CONVERSATION", "amount": 0.0, "rev": 0.0},
        {"stage": "APPLICATION_SUBMITTED", "amount": 250000.0, "rev": 0.0},
        {"stage": "FACILITY_FUNDED", "amount": 250000.0, "rev": 7500.0}
    ]

    for item in outcomes_to_test:
        payload = {
            "crn": crn,
            "outcome_stage": item["stage"],
            "facility_type": "invoice_discounting",
            "funded_amount_gbp": item["amount"],
            "realized_revenue_gbp": item["rev"],
            "time_to_outcome_days": 14.0,
            "total_investigation_cost_gbp": 1.25,
            "feedback_notes": f"Progressed to {item['stage']}"
        }
        res = client.post("/api/v1/adaptive/outcomes", json=payload, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "success"

    # Verify metrics endpoint
    metrics_resp = client.get("/api/v1/adaptive/outcomes/metrics")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["total_outcomes_recorded"] >= 3
    assert metrics["funded_facilities_count"] >= 1
    assert metrics["total_funded_volume_gbp"] >= 250000.0
    assert metrics["total_realized_revenue_gbp"] >= 7500.0
    assert metrics["net_commercial_roi_gbp"] > 0.0

    db.close()

# 6. Machine Learning Shadow Mode & Training Pipeline

def test_machine_learning_shadow_mode_and_calibration():
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    headers = get_auth_headers(role="admin", user_id=admin.id)

    # Trigger ML training endpoint
    train_resp = client.post("/api/v1/adaptive/ml/train", headers=headers)
    assert train_resp.status_code == 200
    train_data = train_resp.json()
    assert "model_version" in train_data
    assert "v1.1.0-ml-shadow" in train_data["model_version"]

    # Verify ML inference via assessment
    ml_model = MachineLearningOpportunityModel(db)
    from backend.app.services.feature_store import FeatureStoreService
    fs = FeatureStoreService(db)
    snap = fs.compute_feature_snapshot("08931201")
    pred = ml_model.predict_opportunity_probability(snap)

    assert "model_version" in pred
    assert "p_funded_ml" in pred
    assert 0.0 <= pred["p_funded_ml"] <= 1.0
    assert pred["shadow_mode"] is True

    # Clean up test-generated outcomes so real DB remains clean
    db.query(CommercialFunnelOutcome).delete()
    db.commit()
    db.close()

    if os.path.exists("ml_models/opportunity_classifier_v1.pkl"):
        try:
            os.remove("ml_models/opportunity_classifier_v1.pkl")
        except OSError:
            pass

# 7. CRN Normalization & RBAC Security

def test_adaptive_api_rbac_and_validation():
    # Invalid CRN returns 400
    headers = get_auth_headers(role="broker")
    resp = client.post("/api/v1/adaptive/investigate/evaluate/INVALID_CRN_XYZ", headers=headers)
    assert resp.status_code == 400
    assert "Invalid UK CRN" in resp.json()["detail"]

    # Unauthenticated request blocked
    resp = client.post("/api/v1/adaptive/investigate/evaluate/08931201")
    assert resp.status_code == 401

    # Non-admin user blocked from training ML model
    broker_headers = get_auth_headers(role="broker")
    resp = client.post("/api/v1/adaptive/ml/train", headers=broker_headers)
    assert resp.status_code == 403
    assert "Operation requires one of the following roles" in resp.json()["detail"]


def test_free_public_tools_zero_cost_and_execution():
    """Verify free public tools (Contracts Finder and Gazette) are cost-free and executable."""
    tools = approved_tool_registry.list_tools()
    tool_map = {t.tool_name: t for t in tools}

    assert "contracts_finder_awards_lookup" in tool_map
    assert tool_map["contracts_finder_awards_lookup"].cost_gbp == 0.00
    assert tool_map["contracts_finder_awards_lookup"].requires_paid_subscription is False

    assert "the_gazette_insolvency_check" in tool_map
    assert tool_map["the_gazette_insolvency_check"].cost_gbp == 0.00
    assert tool_map["the_gazette_insolvency_check"].requires_paid_subscription is False

    db = SessionLocal()
    crn = "08931201"
    # Execute contracts finder lookup
    res_cf = approved_tool_registry.execute_tool("contracts_finder_awards_lookup", crn, db)
    assert res_cf["success"] is True
    assert res_cf["actual_cost_gbp"] == 0.00

    # Execute gazette check
    res_gz = approved_tool_registry.execute_tool("the_gazette_insolvency_check", crn, db)
    assert res_gz["success"] is True
    assert res_gz["actual_cost_gbp"] == 0.00
    db.close()

