import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.signal import NormalizedSignal
from backend.app.models.investigation import InvestigationDecision
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.tool_registry import approved_tool_registry, ToolDefinition

# Configurable default daily limits
DEFAULT_COMPANY_DAILY_BUDGET_GBP = 5.00
DEFAULT_BASE_FACILITY_COMMISSION_ESTIMATE_GBP = 2500.00  # Typical UK SME working-capital broker fee (2% on £125k)

class ValueOfInformationEngine:
    """
    Evaluates Expected Value of Information (EVOI) for candidate external API queries.
    Prevents indiscriminate, wasteful querying and enforces strict commercial utility budgeting.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_company_expenditure_today_gbp(self, crn: str) -> float:
        """Calculates total API fees incurred for this company in the trailing 24 hours."""
        t_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        total = self.db.query(func.sum(InvestigationDecision.actual_cost_gbp)).filter(
            InvestigationDecision.crn == crn,
            InvestigationDecision.created_at >= t_24h,
            InvestigationDecision.status == "executed"
        ).scalar()
        return float(total or 0.0)

    def evaluate_investigation_opportunities(
        self,
        crn: str,
        company_daily_budget_gbp: float = DEFAULT_COMPANY_DAILY_BUDGET_GBP
    ) -> List[InvestigationDecision]:
        """
        Evaluates EVOI across all approved tools for a given company.
        Determines whether to INVESTIGATE, DEFER, STOP, or ESCALATE_HUMAN_REVIEW.
        """
        fs = FeatureStoreService(self.db)
        snapshot = fs.compute_feature_snapshot(crn)
        expenditure_today = self.get_company_expenditure_today_gbp(crn)
        budget_remaining = max(0.0, company_daily_budget_gbp - expenditure_today)

        decisions: List[InvestigationDecision] = []
        now = datetime.now(timezone.utc)
        t_24h = now - timedelta(hours=24)

        for tool in approved_tool_registry.list_tools():
            # 1. Check if tool was executed recently for this company
            recent_exec = self.db.query(InvestigationDecision).filter(
                InvestigationDecision.crn == crn,
                InvestigationDecision.tool_name == tool.tool_name,
                InvestigationDecision.status == "executed",
                InvestigationDecision.created_at >= t_24h
            ).first()

            if recent_exec:
                # Information is already fresh; zero marginal utility
                dec = InvestigationDecision(
                    crn=crn,
                    tool_name=tool.tool_name,
                    evoi_value=0.0,
                    estimated_cost_gbp=tool.cost_gbp,
                    actual_cost_gbp=0.0,
                    uncertainty_prior=0.2,
                    uncertainty_posterior_expected=0.2,
                    decision="STOP",
                    decision_rationale=f"Information from {tool.tool_name} was already retrieved {recent_exec.created_at.strftime('%H:%M UTC')}. Re-querying offers zero marginal value.",
                    budget_remaining_gbp=budget_remaining,
                    status="skipped"
                )
                self.db.add(dec)
                decisions.append(dec)
                continue

            # 2. Compute Expected Information Gain & Uncertainty Reduction
            info_gain, prior_uncertainty, expected_posterior_uncertainty = self._estimate_tool_information_gain(tool, snapshot)

            # 3. Decision Utility Calculation:
            # Probability that new evidence alters the broker's action threshold
            from backend.app.ml.evoi_learning import get_learned_p_action_change
            p_prior = min(0.65, info_gain * 1.5)
            p_action_change, n_runs, is_learned = get_learned_p_action_change(
                self.db,
                tool_name=tool.tool_name,
                snapshot=snapshot,
                default_prior=p_prior,
                min_samples=20
            )
            expected_payoff_delta = p_action_change * DEFAULT_BASE_FACILITY_COMMISSION_ESTIMATE_GBP * 0.05
            
            # Net EVOI: Expected utility gain minus API query fee
            evoi_net = round(expected_payoff_delta - tool.cost_gbp, 2)
            learned_tag = f" (empirical P(change)={p_action_change:.2f} based on {n_runs} runs)" if is_learned else ""

            # 4. Budget & Policy Evaluation
            from backend.app.models.score import LiquidityScoreRecord
            score_rec = self.db.query(LiquidityScoreRecord).filter(LiquidityScoreRecord.crn == crn).order_by(LiquidityScoreRecord.scored_at.desc()).first()
            is_company_qualified = (score_rec and score_rec.score >= 50.0) or (snapshot.charges_satisfied_90d or 0) > 0 or (snapshot.dbt_current_days or 0) >= 15.0

            if tool.tool_name in ("companies_house_officers_lookup", "companies_house_psc_lookup", "decision_maker_identity_verify") and not is_company_qualified:
                decision = "STOP"
                rationale = "Company opportunity unverified (low strain score). Person discovery skipped to prevent unnecessary personal data processing."
            elif tool.cost_gbp > budget_remaining and tool.cost_gbp > 0.0:
                decision = "DEFER"
                rationale = f"Estimated API cost (£{tool.cost_gbp:.2f}) exceeds remaining 24h budget (£{budget_remaining:.2f}). Deferred to next budget window."
            elif evoi_net > 0.0 and info_gain >= 0.15:
                decision = "INVESTIGATE"
                rationale = f"Positive commercial EVOI (+£{evoi_net:.2f}){learned_tag}. High expected uncertainty reduction ({prior_uncertainty:.2f} -> {expected_posterior_uncertainty:.2f})."
            elif prior_uncertainty >= 0.6 and tool.cost_gbp >= 2.0:
                decision = "ESCALATE_HUMAN_REVIEW"
                rationale = f"High uncertainty with substantial API cost (£{tool.cost_gbp:.2f}). Requires broker approval prior to external credit bureau expenditure."
            else:
                decision = "STOP"
                rationale = f"Marginal EVOI insufficient (£{evoi_net:.2f}) to justify API query cost (£{tool.cost_gbp:.2f}). Baseline evidence adequate."

            dec = InvestigationDecision(
                crn=crn,
                tool_name=tool.tool_name,
                evoi_value=evoi_net,
                estimated_cost_gbp=tool.cost_gbp,
                actual_cost_gbp=0.0,
                uncertainty_prior=round(prior_uncertainty, 2),
                uncertainty_posterior_expected=round(expected_posterior_uncertainty, 2),
                decision=decision,
                decision_rationale=rationale,
                budget_remaining_gbp=budget_remaining,
                status="pending" if decision == "INVESTIGATE" else "skipped"
            )
            self.db.add(dec)
            decisions.append(dec)

        self.db.commit()
        return decisions

    def evaluate_single_tool(
        self,
        crn: str,
        tool_name: str,
        remaining_budget: float = DEFAULT_COMPANY_DAILY_BUDGET_GBP
    ) -> InvestigationDecision:
        tool = approved_tool_registry.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Unknown tool: {tool_name}")

        now = datetime.now(timezone.utc)
        t_24h = now - timedelta(hours=24)

        recent_exec = self.db.query(InvestigationDecision).filter(
            InvestigationDecision.crn == crn,
            InvestigationDecision.tool_name == tool.tool_name,
            InvestigationDecision.status == "executed",
            InvestigationDecision.created_at >= t_24h
        ).first()

        from backend.app.models.signal import NormalizedSignal
        recent_signal = self.db.query(NormalizedSignal).filter(
            NormalizedSignal.crn == crn,
            NormalizedSignal.source_provider == tool.provider_name,
            NormalizedSignal.observation_timestamp >= t_24h
        ).first()

        if recent_exec or recent_signal:
            dec = InvestigationDecision(
                crn=crn,
                tool_name=tool.tool_name,
                evoi_value=0.0,
                estimated_cost_gbp=tool.cost_gbp,
                actual_cost_gbp=0.0,
                uncertainty_prior=0.2,
                uncertainty_posterior_expected=0.2,
                decision="STOP",
                decision_rationale=f"Information from {tool.tool_name} was recently refreshed. Re-querying offers zero marginal value.",
                budget_remaining_gbp=remaining_budget,
                status="skipped"
            )
            self.db.add(dec)
            self.db.commit()
            return dec

        fs = FeatureStoreService(self.db)
        snapshot = fs.compute_feature_snapshot(crn)
        info_gain, prior_uncertainty, expected_posterior_uncertainty = self._estimate_tool_information_gain(tool, snapshot)
        
        from backend.app.ml.evoi_learning import get_learned_p_action_change
        p_prior = min(0.65, info_gain * 1.5)
        p_action_change, n_runs, is_learned = get_learned_p_action_change(
            self.db,
            tool_name=tool.tool_name,
            snapshot=snapshot,
            default_prior=p_prior,
            min_samples=20
        )
        expected_payoff_delta = p_action_change * DEFAULT_BASE_FACILITY_COMMISSION_ESTIMATE_GBP * 0.05
        evoi_net = round(expected_payoff_delta - tool.cost_gbp, 2)
        learned_tag = f" (empirical P(change)={p_action_change:.2f} based on {n_runs} runs)" if is_learned else ""

        from backend.app.models.score import LiquidityScoreRecord
        score_rec = self.db.query(LiquidityScoreRecord).filter(LiquidityScoreRecord.crn == crn).order_by(LiquidityScoreRecord.scored_at.desc()).first()
        is_company_qualified = (score_rec and score_rec.score >= 50.0) or (snapshot.charges_satisfied_90d or 0) > 0 or (snapshot.dbt_current_days or 0) >= 15.0

        if tool.tool_name in ("companies_house_officers_lookup", "companies_house_psc_lookup", "decision_maker_identity_verify") and not is_company_qualified:
            decision = "STOP"
            rationale = "Company opportunity unverified (low strain score). Person discovery skipped to prevent unnecessary personal data processing."
        elif tool.cost_gbp > remaining_budget and tool.cost_gbp > 0.0:
            decision = "DEFER"
            rationale = f"Estimated API cost (£{tool.cost_gbp:.2f}) exceeds remaining 24h budget (£{remaining_budget:.2f}). Deferred to next budget window."
        elif evoi_net > 0.0 and info_gain >= 0.15:
            decision = "INVESTIGATE"
            rationale = f"Positive commercial EVOI (+£{evoi_net:.2f}){learned_tag}. High expected uncertainty reduction ({prior_uncertainty:.2f} -> {expected_posterior_uncertainty:.2f})."
        elif prior_uncertainty >= 0.6 and tool.cost_gbp >= 2.0:
            decision = "ESCALATE_HUMAN_REVIEW"
            rationale = f"High uncertainty with substantial API cost (£{tool.cost_gbp:.2f}). Requires broker approval prior to external credit bureau expenditure."
        else:
            decision = "STOP"
            rationale = f"Marginal EVOI insufficient (£{evoi_net:.2f}) to justify API query cost (£{tool.cost_gbp:.2f}). Baseline evidence adequate."

        dec = InvestigationDecision(
            crn=crn,
            tool_name=tool.tool_name,
            evoi_value=evoi_net,
            estimated_cost_gbp=tool.cost_gbp,
            actual_cost_gbp=0.0,
            uncertainty_prior=round(prior_uncertainty, 2),
            uncertainty_posterior_expected=round(expected_posterior_uncertainty, 2),
            decision=decision,
            decision_rationale=rationale,
            budget_remaining_gbp=remaining_budget,
            status="pending" if decision == "INVESTIGATE" else "skipped"
        )
        self.db.add(dec)
        self.db.commit()
        return dec

    def _estimate_tool_information_gain(
        self,
        tool: ToolDefinition,
        snapshot: CompanyFeatureSnapshot
    ) -> Tuple[float, float, float]:
        """
        Estimates expected entropy reduction / information gain based on existing feature missingness.
        Returns: (info_gain, prior_uncertainty, expected_posterior_uncertainty)
        """
        if tool.tool_name == "companies_house_charges_refresh":
            # If recent MR04 satisfaction exists without recent check, high info gain
            if (snapshot.charges_satisfied_90d or 0) > 0:
                return 0.35, 0.40, 0.15
            return 0.15, 0.25, 0.10

        elif tool.tool_name == "creditsafe_deep_financials":
            # If DBT data is missing or stale, high information gain
            if snapshot.dbt_current_days is None:
                return 0.50, 0.65, 0.20
            return 0.10, 0.20, 0.10

        elif tool.tool_name == "experian_delinquency_trace":
            # High gain if DBT is elevated (>15 days) and trade delinquency trajectory is needed
            if (snapshot.dbt_current_days or 0) >= 15.0:
                return 0.45, 0.60, 0.20
            return 0.08, 0.25, 0.17

        elif tool.tool_name == "workforce_hiring_velocity":
            # If headcount growth data is missing
            if snapshot.headcount_growth_90d_pct is None:
                return 0.30, 0.45, 0.20
            return 0.05, 0.15, 0.10

        elif tool.tool_name in ("companies_house_officers_lookup", "companies_house_psc_lookup"):
            from backend.app.models.decision_maker import CompanyOfficer
            active_officers = self.db.query(CompanyOfficer).filter(
                CompanyOfficer.crn == snapshot.crn,
                CompanyOfficer.is_active == True
            ).count()
            if active_officers == 0:
                return 0.45, 0.55, 0.15
            return 0.05, 0.15, 0.10

        elif tool.tool_name == "decision_maker_identity_verify":
            from backend.app.models.decision_maker import ProfessionalRoleProfile
            unverified_dms = self.db.query(ProfessionalRoleProfile).filter(
                ProfessionalRoleProfile.crn == snapshot.crn,
                ProfessionalRoleProfile.verification_status != "validated",
                ProfessionalRoleProfile.is_finance_decision_maker == True
            ).count()
            if unverified_dms > 0:
                return 0.40, 0.50, 0.15
            return 0.05, 0.20, 0.15

        return 0.10, 0.30, 0.20

    def execute_decision(self, decision_id: int, actor_user_id: Optional[str] = None) -> Dict[str, Any]:
        """Executes a previously pending INVESTIGATE decision."""
        dec = self.db.query(InvestigationDecision).filter(InvestigationDecision.id == decision_id).first()
        if not dec:
            return {"success": False, "error": f"Decision {decision_id} not found"}

        if dec.status == "executed":
            return {"success": True, "message": "Decision already executed", "result": dec.result_summary}

        result = approved_tool_registry.execute_tool(
            tool_name=dec.tool_name,
            crn=dec.crn,
            db=self.db,
            actor_user_id=actor_user_id
        )

        dec.executed_at = datetime.now(timezone.utc)
        dec.actual_cost_gbp = result.get("actual_cost_gbp", 0.0)
        if result.get("success"):
            dec.status = "executed"
            dec.result_summary = result.get("summary", "Query completed successfully")
        else:
            dec.status = "failed"
            dec.error_details = result.get("error", "Unknown error during execution")

        self.db.commit()
        return result
