"""
Campaign Economics and Full-Funnel Commercial Modeling Service.
Calculates full-funnel commercial unit economics across Conservative, Base, and Optimistic scenarios,
computes break-even response rates and risk-bounded test budgets, and strictly isolates assumptions.
"""

import logging
from typing import Any, Dict, List, Optional
from backend.app.schemas.marketing import (
    CampaignEconomicsModel,
    ScenarioEconomics,
    ScenarioType,
)

logger = logging.getLogger("warden.campaign_economics")


class CampaignEconomicsService:
    """Calculates full-funnel unit economics and ROI boundaries for commercial campaigns."""

    @classmethod
    def calculate_economics(
        cls,
        facility_size_gbp: float,
        campaign_cost_gbp: float = 0.0,
        target_reach: int = 1000,
        strategy_id: Optional[str] = None,
        is_historical_benchmark: bool = False,
        observed_rates: Optional[Dict[str, float]] = None,
    ) -> CampaignEconomicsModel:
        """
        Generate full-funnel unit economics across three scenario bounds.
        Explicitly isolates assumed conversion benchmarks from observed historical data.
        """
        # Ensure safe floor values
        clean_facility = max(10_000.0, float(facility_size_gbp))
        clean_cost = max(0.0, float(campaign_cost_gbp))
        clean_reach = max(100, int(target_reach))

        # Scenario Parameter Matrices (UK Commercial Invoice Finance Industry Standards)
        scenario_params = {
            ScenarioType.CONSERVATIVE: {
                "engagement_rate": 0.005,
                "qualified_lead_rate": 0.04,
                "application_rate": 0.15,
                "funding_approval_rate": 0.35,
                "broker_commission_rate": 0.010,
            },
            ScenarioType.BASE: {
                "engagement_rate": 0.012,
                "qualified_lead_rate": 0.08,
                "application_rate": 0.25,
                "funding_approval_rate": 0.50,
                "broker_commission_rate": 0.015,
            },
            ScenarioType.OPTIMISTIC: {
                "engagement_rate": 0.025,
                "qualified_lead_rate": 0.15,
                "application_rate": 0.40,
                "funding_approval_rate": 0.70,
                "broker_commission_rate": 0.020,
            },
        }

        # Override with empirical rates if provided (from outcome learning)
        if observed_rates and is_historical_benchmark:
            for st in scenario_params:
                for k, v in observed_rates.items():
                    if k in scenario_params[st]:
                        # Apply proportional scaling around observed mean
                        mult = 0.7 if st == ScenarioType.CONSERVATIVE else (1.3 if st == ScenarioType.OPTIMISTIC else 1.0)
                        scenario_params[st][k] = v * mult

        scenarios_dict: Dict[str, ScenarioEconomics] = {}

        for stype, params in scenario_params.items():
            eng_rate = params["engagement_rate"]
            qual_rate = params["qualified_lead_rate"]
            app_rate = params["application_rate"]
            fund_rate = params["funding_approval_rate"]
            comm_rate = params["broker_commission_rate"]

            # Cohort funnel projections
            engagements = clean_reach * eng_rate
            qualified_leads = engagements * qual_rate
            applications = qualified_leads * app_rate
            funded_deals = applications * fund_rate

            # Commercial Return (Gross commission = funded deals * facility size * commission rate)
            gross_commission = round(funded_deals * clean_facility * comm_rate, 2)
            net_value = round(gross_commission - clean_cost, 2)

            if clean_cost > 0.0:
                roi_multiple: Optional[float] = round(gross_commission / clean_cost, 2)
                roi_status = "COMPUTED"
            else:
                roi_multiple = None
                roi_status = "not_applicable_zero_cost"

            cost_per_qual_lead = (
                round(clean_cost / qualified_leads, 2) if qualified_leads > 0 else 0.0
            )
            cac = round(clean_cost / funded_deals, 2) if funded_deals > 0 else 0.0

            scenarios_dict[stype.value] = ScenarioEconomics(
                scenario_type=stype,
                assumed_facility_size_gbp=clean_facility,
                engagement_rate=eng_rate,
                qualified_lead_rate=qual_rate,
                application_rate=app_rate,
                funding_approval_rate=fund_rate,
                broker_commission_rate=comm_rate,
                target_reach=clean_reach,
                estimated_engagements=round(engagements, 2),
                estimated_qualified_leads=round(qualified_leads, 2),
                estimated_applications=round(applications, 2),
                estimated_funded_deals=round(funded_deals, 4),
                gross_commission_gbp=gross_commission,
                campaign_cost_gbp=clean_cost,
                net_value_gbp=net_value,
                roi_multiple=roi_multiple,
                roi_status=roi_status,
                cost_per_qualified_lead_gbp=cost_per_qual_lead,
                customer_acquisition_cost_gbp=cac,
            )

        # Base scenario reference for break-even calculations
        base_sc = scenarios_dict[ScenarioType.BASE.value]
        base_comm_per_deal = clean_facility * base_sc.broker_commission_rate

        if clean_cost > 0 and base_comm_per_deal > 0:
            be_funded_deals = clean_cost / base_comm_per_deal
            # Conversion multiplier from reach to funded deal:
            reach_to_deal_rate = (
                base_sc.qualified_lead_rate
                * base_sc.application_rate
                * base_sc.funding_approval_rate
            )
            if reach_to_deal_rate > 0 and clean_reach > 0:
                be_response_rate = be_funded_deals / (clean_reach * reach_to_deal_rate)
            else:
                be_response_rate = 0.0
            be_qualified_leads = clean_cost / max(1.0, (base_sc.application_rate * base_sc.funding_approval_rate * base_comm_per_deal))
        else:
            be_funded_deals = 0.0
            be_response_rate = 0.0
            be_qualified_leads = 0.0

        # Weighted Expected Net Value (25% Conservative, 50% Base, 25% Optimistic)
        expected_net_value = round(
            0.25 * scenarios_dict[ScenarioType.CONSERVATIVE.value].net_value_gbp
            + 0.50 * scenarios_dict[ScenarioType.BASE.value].net_value_gbp
            + 0.25 * scenarios_dict[ScenarioType.OPTIMISTIC.value].net_value_gbp,
            2,
        )

        # Max recommended validation test budget (capped at 25% of Base Gross Commission or £500 max test ceiling)
        max_test_budget = round(
            min(500.0, max(50.0, 0.25 * base_sc.gross_commission_gbp)), 2
        ) if clean_cost == 0.0 else round(min(clean_cost, 0.50 * base_sc.gross_commission_gbp), 2)

        # Explicit isolation of assumptions
        assumptions = [
            f"Modeled borrowing base / facility size: £{clean_facility:,.2f}",
            "Broker commission modeled between 1.0% and 2.0% in line with standard UK NACFB commercial agreements",
            "Funnel stage conversions reflect UK B2B commercial finance industry benchmarks",
            "Assumptions are isolated and unobserved for target firm until verified empirical outcomes are reported",
            f"Target cohort size modeled at {clean_reach:,} businesses in identified sector",
        ]
        if is_historical_benchmark:
            assumptions.append("Calibrated against empirical historical conversions via Outcome Learning Agent")

        # Risk assessment formulation
        if expected_net_value > 2_000.0:
            risk = "FAVOURABLE: Expected commercial return substantially exceeds activation threshold"
        elif expected_net_value > 0.0:
            risk = "MODERATE: Positive expected value; requires low-cost test plan to confirm engagement rate"
        else:
            risk = "ELEVATED_RISK: Negative or borderline expected net value; recommend zero-cost validation only"

        evidence_basis = (
            "EMPIRICAL_OBSERVED"
            if (is_historical_benchmark and observed_rates)
            else "ILLUSTRATIVE_PRIOR_BENCHMARK"
        )

        return CampaignEconomicsModel(
            strategy_id=strategy_id,
            indicative_facility_size_gbp=clean_facility,
            campaign_cost_gbp=clean_cost,
            target_reach=clean_reach,
            scenarios=scenarios_dict,
            break_even_qualified_leads=round(be_qualified_leads, 2),
            break_even_response_rate=round(be_response_rate, 4),
            max_recommended_test_budget_gbp=max_test_budget,
            expected_net_value_gbp=expected_net_value,
            is_historical_benchmark=is_historical_benchmark,
            evidence_basis=evidence_basis,
            assumptions_log=assumptions,
            risk_assessment=risk,
        )
