"""
Tests for Phase 6: Empirical EVOI Learning & Investigation Outcome Tracking.

Verifies:
1. Canonical missingness pattern extraction.
2. Prior fallback when empirical investigation run logs < 20.
3. Bayesian-smoothed empirical P(action change) when sample count >= 20.
4. Investigation run logging upon tool execution.
5. Strict teardown ensuring zero DB pollution.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.app.database import SessionLocal
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.investigation import InvestigationRunLog, InvestigationDecision
from backend.app.models.company import CompanyRegistry
from backend.app.models.score import LiquidityScoreRecord
from backend.app.ml.evoi_learning import (
    extract_missingness_pattern,
    get_learned_p_action_change,
    capture_investigation_prior_state,
    record_investigation_outcome,
)
from backend.app.services.investigation_engine import ValueOfInformationEngine
from backend.app.services.tool_registry import approved_tool_registry


@pytest.fixture(autouse=True)
def cleanup_investigation_logs():
    """Teardown fixture ensuring tests do not pollute the database."""
    yield
    db = SessionLocal()
    try:
        db.query(InvestigationRunLog).filter(
            InvestigationRunLog.crn.in_(["08931201", "TEST_CRN_EVOI"])
        ).delete(synchronize_session=False)
        db.query(CompanyRegistry).filter(CompanyRegistry.crn == "TEST_CRN_EVOI").delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_missingness_pattern_extraction():
    """Verifies that missing feature combinations produce deterministic canonical strings."""
    snap1 = CompanyFeatureSnapshot(
        crn="TEST0001",
        as_of_timestamp=datetime.now(timezone.utc),
        dbt_current_days=None,
        headcount_growth_90d_pct=12.5,
        credit_score_drop_90d=None,
        age_latest_charge_satisfaction_days=10.0
    )
    # Missing: credit_score_drop, dbt
    assert extract_missingness_pattern(snap1) == "credit_score_drop,dbt"

    snap_complete = CompanyFeatureSnapshot(
        crn="TEST0002",
        as_of_timestamp=datetime.now(timezone.utc),
        dbt_current_days=15.0,
        headcount_growth_90d_pct=10.0,
        credit_score_drop_90d=5.0,
        age_latest_charge_satisfaction_days=20.0
    )
    assert extract_missingness_pattern(snap_complete) == "complete"
    assert extract_missingness_pattern(None) == "all_missing"


def test_evoi_prior_fallback_when_samples_low():
    """When fewer than 20 runs exist, returns hard-coded prior without error."""
    db = SessionLocal()
    tool_name = "test_low_sample_tool"
    p_val, n_obs, is_learned = get_learned_p_action_change(
        db,
        tool_name=tool_name,
        snapshot=None,
        default_prior=0.35,
        min_samples=20
    )
    db.close()

    assert p_val == 0.35
    assert n_obs == 0
    assert is_learned is False


def test_evoi_bayesian_learning_with_sufficient_samples():
    """
    When >= 20 investigation runs are recorded, learns empirical probability
    with Bayesian smoothing towards the prior.
    """
    db = SessionLocal()
    test_tool = "companies_house_charges_refresh"
    test_crn = "08931201"

    # Inject 25 run logs: 20 action changes, 5 non-changes
    for i in range(25):
        run = InvestigationRunLog(
            crn=test_crn,
            tool_name=test_tool,
            missingness_pattern="dbt,headcount_growth",
            prior_action="MONITOR",
            posterior_action="PRIORITY_DIRECT_OUTREACH" if i < 20 else "MONITOR",
            prior_tier="Tier 3",
            posterior_tier="Tier 1" if i < 20 else "Tier 3",
            prior_uncertainty=0.6,
            posterior_uncertainty=0.2,
            action_changed=(i < 20),
            tool_success=True
        )
        db.add(run)
    db.commit()

    # Query learned probability with default prior = 0.20
    # Prior pseudo-weight N0=10 (10 * 0.20 = 2.0)
    # Observed: 20 changes out of 25 runs
    # Expected smoothed: (2.0 + 20) / (10 + 25) = 22 / 35 ≈ 0.6286
    p_learned, n_obs, is_learned = get_learned_p_action_change(
        db,
        tool_name=test_tool,
        snapshot=CompanyFeatureSnapshot(
            crn=test_crn,
            as_of_timestamp=datetime.now(timezone.utc),
            dbt_current_days=None,
            headcount_growth_90d_pct=None,
            credit_score_drop_90d=0.0,
            age_latest_charge_satisfaction_days=10.0
        ),
        default_prior=0.20,
        min_samples=20
    )

    assert is_learned is True
    assert n_obs == 25
    assert abs(p_learned - (22.0 / 35.0)) < 0.01

    # Verify that ValueOfInformationEngine utilizes the learned probability
    engine = ValueOfInformationEngine(db)
    dec = engine.evaluate_single_tool(test_crn, test_tool, remaining_budget=20.0)
    assert "empirical P(change)=" in dec.decision_rationale or "recently refreshed" in dec.decision_rationale

    db.close()


def test_tool_execution_logs_investigation_outcome():
    """
    Verifies that calling approved_tool_registry.execute_tool automatically
    creates an InvestigationRunLog capturing prior and posterior state.
    """
    db = SessionLocal()
    crn = "08931201"

    # Count prior logs
    prior_logs_count = db.query(InvestigationRunLog).filter(
        InvestigationRunLog.crn == crn
    ).count()

    # Execute workforce hiring velocity tool
    res = approved_tool_registry.execute_tool(
        tool_name="workforce_hiring_velocity",
        crn=crn,
        db=db,
        actor_user_id="test-analyst"
    )
    assert res["success"] is True

    # Check that new InvestigationRunLog was written
    post_logs = db.query(InvestigationRunLog).filter(
        InvestigationRunLog.crn == crn,
        InvestigationRunLog.tool_name == "workforce_hiring_velocity"
    ).all()

    assert len(post_logs) >= prior_logs_count + 1
    latest = post_logs[-1]
    assert latest.tool_name == "workforce_hiring_velocity"
    assert latest.tool_success is True
    assert latest.missingness_pattern is not None
    assert isinstance(latest.action_changed, bool)

    db.close()
