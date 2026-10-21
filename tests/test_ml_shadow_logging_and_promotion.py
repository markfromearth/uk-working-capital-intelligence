import os
import json
import pytest
from datetime import datetime, timezone
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.models.model_prediction import ModelPrediction
from backend.app.models.investigation import CommercialOpportunityAssessment
from backend.app.core.security import create_access_token, hash_password
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.opportunity_assessment import CommercialOpportunityAssessor
from backend.app.ml.promotion import bootstrap_cluster_ci, get_latest_model_card

client = TestClient(app)


def get_auth_headers(role: str = "broker", user_id: str = "test-user-id"):
    token = create_access_token(subject=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def setup_shadow_env():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)

    admin = db.query(User).filter(User.email == "admin_shadow@test.com").first()
    if not admin:
        admin = User(
            email="admin_shadow@test.com",
            hashed_password=hash_password("AdminSecure2026!"),
            full_name="Admin Shadow Tester",
            role="admin",
            is_active=1
        )
        db.add(admin)
        db.commit()

    db.close()


def test_shadow_prediction_logging_on_assessment():
    """
    Verifies that whenever a commercial opportunity assessment is produced,
    a corresponding ModelPrediction row is automatically persisted with both
    ML p_value and rules_p_value, alongside the feature vector JSON.
    """
    db = SessionLocal()
    crn = "08931201"
    assessor = CommercialOpportunityAssessor(db)

    # Initial prediction count
    pre_count = db.query(ModelPrediction).filter(ModelPrediction.crn == crn).count()

    # Produce assessment
    assessment = assessor.assess_company(crn)
    assert assessment is not None

    # Check ModelPrediction row created
    post_preds = db.query(ModelPrediction).filter(ModelPrediction.crn == crn).order_by(ModelPrediction.created_at.desc()).all()
    assert len(post_preds) == pre_count + 1

    latest_pred = post_preds[0]
    assert latest_pred.crn == crn
    assert latest_pred.model_version is not None
    assert 0.0 <= latest_pred.p_value <= 1.0
    assert 0.0 <= latest_pred.rules_p_value <= 1.0
    assert latest_pred.feature_vector_json is not None

    # Verify JSON parses to feature dict
    f_dict = json.loads(latest_pred.feature_vector_json)
    assert isinstance(f_dict, dict)
    assert "charges_satisfied_90d" in f_dict

    db.close()


def test_bootstrap_cluster_ci_evaluation():
    """
    Verifies clustered bootstrap CI calculation by company:
    Evaluates whether candidate improvement over rules baseline is statistically significant.
    """
    # 10 companies, 4 observations each = 40 rows
    crns = []
    for c in range(10):
        crns.extend([f"CRN_{c}"] * 4)

    y_true = [1 if i % 3 == 0 else 0 for i in range(40)]
    # Perfect candidate model
    cand_scores = [0.95 if y == 1 else 0.05 for y in y_true]
    # Inverted/poor rules baseline
    rules_scores = [0.20 if y == 1 else 0.80 for y in y_true]

    ci_res = bootstrap_cluster_ci(
        y_true=y_true,
        cand_scores=cand_scores,
        rules_scores=rules_scores,
        crns=crns,
        num_bootstraps=200,
        k=5,
        seed=123
    )

    assert ci_res["bootstrap_ci_valid"] is True
    assert ci_res["cluster_companies_count"] == 10
    assert ci_res["precision_excludes_zero"] is True
    assert ci_res["brier_excludes_zero"] is True
    assert ci_res["ci_statistically_significant"] is True


def test_ml_status_endpoint_and_manual_promotion_workflow(tmp_path):
    """
    Verifies:
    1. GET /api/v1/adaptive/ml/status returns volume, label counts, and promotion gate status.
    2. POST /api/v1/adaptive/ml/promote enforces ADMIN RBAC and audit logging.
    3. Blocks promotion without passing gate or explicit override.
    """
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    broker = db.query(User).filter(User.role == "broker").first()
    admin_headers = get_auth_headers(role="admin", user_id=admin.id)
    broker_headers = get_auth_headers(role="broker", user_id=broker.id if broker else admin.id)
    db.close()

    # 1. GET /ml/status
    res = client.get("/api/v1/adaptive/ml/status", headers=broker_headers)
    assert res.status_code == 200
    status_data = res.json()

    assert status_data["active_mode"] == "shadow_mode"
    assert "data_volume" in status_data
    assert "registered_companies" in status_data["data_volume"]
    assert "label_counts" in status_data
    assert "promotion_gate" in status_data
    assert status_data["promotion_gate"]["manual_promotion_required"] is True
    assert status_data["promotion_gate"]["auto_promotion_allowed"] is False

    # 2. Broker cannot promote (RBAC)
    resp_broker_promote = client.post("/api/v1/adaptive/ml/promote", json={}, headers=broker_headers)
    assert resp_broker_promote.status_code == 403

    # 3. Admin promote when no evaluated model card exists returns 400
    # Create temporary mock card directory
    test_version_dir = os.path.join("ml_models", "v2.0.0-test-promotion")
    os.makedirs(test_version_dir, exist_ok=True)
    card_path = os.path.join(test_version_dir, "metrics.json")

    mock_card = {
        "model_version": "v2.0.0-test-promotion",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": "abc1234",
        "feature_spec_hash": "hash123",
        "promotion_gate": {
            "passed": False, # Gate failed!
            "criteria": "Top-10 precision and Brier improvement"
        }
    }
    with open(card_path, "w") as f:
        json.dump(mock_card, f)

    try:
        # Promotion rejected because gate not passed and no override reason provided
        resp_fail = client.post("/api/v1/adaptive/ml/promote", json={}, headers=admin_headers)
        assert resp_fail.status_code == 400
        assert "Promotion gate criteria NOT met" in resp_fail.json()["detail"]

        # Promotion succeeds with documented administrative override
        resp_override = client.post(
            "/api/v1/adaptive/ml/promote",
            json={"override_reason": "Emergency controlled pilot approved by Head of Credit"},
            headers=admin_headers
        )
        assert resp_override.status_code == 200
        assert resp_override.json()["status"] == "success"
        assert resp_override.json()["mode"] == "promoted_with_admin_override"
        assert resp_override.json()["audit_logged"] is True

    finally:
        if os.path.exists(card_path):
            os.remove(card_path)
        if os.path.exists(test_version_dir):
            os.rmdir(test_version_dir)
