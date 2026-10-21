"""
Empirical EVOI (Expected Value of Information) Learning Module.

Replaces static, guessed information-gain constants with empirically estimated probabilities
of action and tier change: P(action change | tool, missingness_pattern).

Enforces Bayesian smoothing with prior fallbacks when empirical sample counts are small (< 20 runs).
"""

from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.logging import logger
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.investigation import InvestigationRunLog
from backend.app.models.score import LiquidityScoreRecord
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.opportunity_assessment import CommercialOpportunityAssessor


def extract_missingness_pattern(snapshot: Optional[CompanyFeatureSnapshot]) -> str:
    """
    Constructs a deterministic canonical pattern representing which key features
    are missing for a company snapshot.
    """
    if snapshot is None:
        return "all_missing"

    missing = []
    if snapshot.dbt_current_days is None:
        missing.append("dbt")
    if snapshot.headcount_growth_90d_pct is None:
        missing.append("headcount_growth")
    if snapshot.credit_score_drop_90d is None:
        missing.append("credit_score_drop")
    if snapshot.age_latest_charge_satisfaction_days is None:
        missing.append("charge_satisfaction")

    if not missing:
        return "complete"
    return ",".join(sorted(missing))


def capture_investigation_prior_state(db: Session, crn: str) -> Dict[str, Any]:
    """
    Captures the decision-making state of a company PRIOR to executing an investigation tool.
    Records feature missingness pattern, current tier, recommended action, and uncertainty.
    """
    try:
        fs = FeatureStoreService(db)
        snapshot = fs.compute_feature_snapshot(crn)
        missingness = extract_missingness_pattern(snapshot)

        score_rec = db.query(LiquidityScoreRecord).filter(
            LiquidityScoreRecord.crn == crn
        ).order_by(LiquidityScoreRecord.scored_at.desc()).first()
        prior_tier = score_rec.score_band if score_rec else None

        assessor = CommercialOpportunityAssessor(db)
        prior_assessment = assessor.assess_company(crn)

        return {
            "crn": crn,
            "missingness_pattern": missingness,
            "prior_tier": prior_tier,
            "prior_action": prior_assessment.recommended_action if prior_assessment else None,
            "prior_uncertainty": float(prior_assessment.evidence_uncertainty) if prior_assessment else 0.5
        }
    except Exception as e:
        logger.warning(f"Failed to capture investigation prior state for {crn}: {e}")
        return {
            "crn": crn,
            "missingness_pattern": "unknown",
            "prior_tier": None,
            "prior_action": None,
            "prior_uncertainty": 0.5
        }


def record_investigation_outcome(
    db: Session,
    investigation_id: Optional[int],
    crn: str,
    tool_name: str,
    prior_state: Dict[str, Any],
    tool_success: bool = True
) -> Optional[InvestigationRunLog]:
    """
    Captures POSTERIOR decision-making state after an investigation tool execution and logs
    whether the new evidence triggered an action or tier change.
    """
    try:
        score_rec = db.query(LiquidityScoreRecord).filter(
            LiquidityScoreRecord.crn == crn
        ).order_by(LiquidityScoreRecord.scored_at.desc()).first()
        post_tier = score_rec.score_band if score_rec else None

        assessor = CommercialOpportunityAssessor(db)
        post_assessment = assessor.assess_company(crn)
        post_action = post_assessment.recommended_action if post_assessment else None
        post_uncertainty = float(post_assessment.evidence_uncertainty) if post_assessment else 0.5

        prior_action = prior_state.get("prior_action")
        prior_tier = prior_state.get("prior_tier")
        prior_uncertainty = prior_state.get("prior_uncertainty", 0.5)

        # Detect if commercial action or risk tier materially changed
        action_changed = False
        if prior_action and post_action and prior_action != post_action:
            action_changed = True
        elif prior_tier and post_tier and prior_tier != post_tier:
            action_changed = True

        run_log = InvestigationRunLog(
            investigation_id=investigation_id,
            crn=crn,
            tool_name=tool_name,
            missingness_pattern=prior_state.get("missingness_pattern", "complete"),
            prior_action=prior_action,
            posterior_action=post_action,
            prior_tier=prior_tier,
            posterior_tier=post_tier,
            prior_uncertainty=float(prior_uncertainty),
            posterior_uncertainty=float(post_uncertainty),
            action_changed=action_changed,
            tool_success=tool_success
        )
        db.add(run_log)
        db.commit()
        return run_log
    except Exception as e:
        logger.error(f"Failed to record investigation outcome for {crn} / {tool_name}: {e}")
        db.rollback()
        return None


def get_learned_p_action_change(
    db: Session,
    tool_name: str,
    snapshot: Optional[CompanyFeatureSnapshot] = None,
    default_prior: float = 0.25,
    min_samples: int = 20
) -> Tuple[float, int, bool]:
    """
    Estimates P(action change | tool, missingness_pattern).

    Returns:
        (p_action_change, sample_count, is_learned)
        - If sample_count < min_samples: returns (default_prior, sample_count, False)
        - If sample_count >= min_samples: returns Bayesian-smoothed empirical probability (is_learned=True)
    """
    try:
        pattern = extract_missingness_pattern(snapshot)

        # 1. First check if pattern-specific runs meet minimum sample size
        pattern_query = db.query(InvestigationRunLog).filter(
            InvestigationRunLog.tool_name == tool_name,
            InvestigationRunLog.missingness_pattern == pattern,
            InvestigationRunLog.tool_success == True
        )
        pattern_n = pattern_query.count()

        if pattern_n >= min_samples:
            changed_n = pattern_query.filter(InvestigationRunLog.action_changed == True).count()
            n_obs = pattern_n
        else:
            # Fall back to aggregate runs across this tool
            tool_query = db.query(InvestigationRunLog).filter(
                InvestigationRunLog.tool_name == tool_name,
                InvestigationRunLog.tool_success == True
            )
            tool_n = tool_query.count()
            if tool_n < min_samples:
                # Insufficient evidence: safely fall back to the prior
                return default_prior, tool_n, False

            changed_n = tool_query.filter(InvestigationRunLog.action_changed == True).count()
            n_obs = tool_n

        # Bayesian smoothing: pseudo-prior with weight N0 = 10
        n0 = 10.0
        p_smoothed = (n0 * default_prior + changed_n) / (n0 + n_obs)
        p_clamped = max(0.02, min(0.90, p_smoothed))

        return round(p_clamped, 4), n_obs, True

    except Exception as e:
        logger.warning(f"Error querying learned EVOI for {tool_name}: {e}")
        return default_prior, 0, False
