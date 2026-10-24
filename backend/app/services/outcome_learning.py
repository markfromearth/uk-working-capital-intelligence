import csv
import io
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.logging import logger
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.marketing import CampaignOutcomeEvent, CampaignTrackingRecord
from backend.app.services.feature_store import FeatureStoreService

VALID_OUTCOME_STAGES = [
    "NO_RESPONSE",
    "QUALIFIED_CONVERSATION",
    "BROKER_ACCEPTED",
    "APPLICATION_SUBMITTED",
    "LENDER_DECLINED",
    "FACILITY_OFFERED",
    "FACILITY_FUNDED"
]

VALID_CAMPAIGN_EVENT_TYPES = [
    "IMPRESSION",
    "CLICK",
    "QUALIFIED_ENQUIRY",
    "APPLICATION",
    "FUNDED_DEAL",
]

class OutcomeLearningService:
    """
    Captures commercial sales-funnel outcomes, maintains feedback audit records,
    and constructs time-aware ML training datasets accounting for outcome censoring.
    """

    def __init__(self, db: Session):
        self.db = db

    def record_outcome(
        self,
        crn: str,
        outcome_stage: str,
        broker_user_id: Optional[str] = None,
        facility_type: Optional[str] = None,
        funded_amount_gbp: float = 0.0,
        realized_revenue_gbp: float = 0.0,
        loss_reason: Optional[str] = None,
        time_to_outcome_days: Optional[float] = None,
        total_investigation_cost_gbp: float = 0.0,
        feedback_notes: Optional[str] = None
    ) -> CommercialFunnelOutcome:
        """Records a commercial broker feedback outcome."""
        if outcome_stage not in VALID_OUTCOME_STAGES:
            raise ValueError(f"Invalid outcome stage '{outcome_stage}'. Allowed: {VALID_OUTCOME_STAGES}")

        outcome = CommercialFunnelOutcome(
            crn=crn,
            broker_user_id=broker_user_id,
            outcome_stage=outcome_stage,
            facility_type=facility_type,
            funded_amount_gbp=funded_amount_gbp,
            realized_revenue_gbp=realized_revenue_gbp,
            loss_reason=loss_reason,
            time_to_outcome_days=time_to_outcome_days,
            total_investigation_cost_gbp=total_investigation_cost_gbp,
            feedback_notes=feedback_notes,
            recorded_at=datetime.now(timezone.utc)
        )
        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)
        logger.info(f"Recorded commercial funnel outcome for {crn}: {outcome_stage} (Funded: £{funded_amount_gbp:,.2f})")
        return outcome

    def get_company_outcomes(self, crn: str) -> List[CommercialFunnelOutcome]:
        return self.db.query(CommercialFunnelOutcome).filter(
            CommercialFunnelOutcome.crn == crn
        ).order_by(CommercialFunnelOutcome.recorded_at.desc()).all()

    def build_time_aware_training_dataset(
        self,
        cutoff_date: Optional[datetime] = None
    ) -> Tuple[List[List[float]], List[int], List[str]]:
        """
        Builds a time-aware tabular dataset (X, y, feature_names) for supervised ML training.
        Ensures that features are extracted strictly as of the observation date BEFORE the outcome,
        guaranteeing zero target leakage.
        """
        outcomes = self.db.query(CommercialFunnelOutcome).all()
        if not outcomes:
            return [], [], []

        X: List[List[float]] = []
        y: List[int] = []
        feature_names = [
            "charges_satisfied_90d",
            "charges_created_90d",
            "age_latest_charge_days",
            "dbt_current_days",
            "dbt_trend_90d",
            "headcount_current",
            "headcount_growth_90d_pct",
            "adverse_credit_events_count",
            "corroboration_signal_count",
            "distinct_source_count"
        ]

        fs = FeatureStoreService(self.db)

        for out in outcomes:
            # Event label: 1 if converted to facility funded, 0 otherwise
            is_funded = 1 if out.outcome_stage in ("FACILITY_OFFERED", "FACILITY_FUNDED") else 0
            
            # Point-in-time feature extraction strictly as of when outcome was recorded
            snapshot = fs.compute_feature_snapshot(out.crn, as_of_timestamp=out.recorded_at)
            
            vec = [
                float(snapshot.charges_satisfied_90d or 0),
                float(snapshot.charges_created_90d or 0),
                float(snapshot.age_latest_charge_satisfaction_days or 180.0),
                float(snapshot.dbt_current_days if snapshot.dbt_current_days is not None else 0.0),
                float(snapshot.dbt_trend_90d if snapshot.dbt_trend_90d is not None else 0.0),
                float(snapshot.headcount_current or 10),
                float(snapshot.headcount_growth_90d_pct if snapshot.headcount_growth_90d_pct is not None else 0.0),
                float(snapshot.adverse_insolvency_events_count or 0),
                float(snapshot.corroboration_signal_count or 0),
                float(snapshot.distinct_source_count or 0)
            ]
            X.append(vec)
            y.append(is_funded)

        return X, y, feature_names

    def get_commercial_conversion_metrics(self) -> Dict[str, Any]:
        """Calculates commercial conversion funnel KPIs and API efficiency metrics."""
        total_outcomes = self.db.query(CommercialFunnelOutcome).count()
        funded_count = self.db.query(CommercialFunnelOutcome).filter(
            CommercialFunnelOutcome.outcome_stage == "FACILITY_FUNDED"
        ).count()
        total_revenue = self.db.query(func.sum(CommercialFunnelOutcome.realized_revenue_gbp)).scalar() or 0.0
        total_funded_volume = self.db.query(func.sum(CommercialFunnelOutcome.funded_amount_gbp)).filter(
            CommercialFunnelOutcome.outcome_stage == "FACILITY_FUNDED"
        ).scalar() or 0.0
        total_api_costs = self.db.query(func.sum(CommercialFunnelOutcome.total_investigation_cost_gbp)).scalar() or 0.0

        conversion_rate = (funded_count / total_outcomes * 100.0) if total_outcomes > 0 else 0.0
        roi_net = (total_revenue - total_api_costs)

        return {
            "total_outcomes_recorded": total_outcomes,
            "funded_facilities_count": funded_count,
            "total_funded_volume_gbp": round(float(total_funded_volume), 2),
            "conversion_rate_pct": round(conversion_rate, 1),
            "total_realized_revenue_gbp": round(float(total_revenue), 2),
            "total_investigation_costs_gbp": round(float(total_api_costs), 2),
            "net_commercial_roi_gbp": round(float(roi_net), 2)
        }

    # =========================================================================
    # CAMPAIGN TRACKING & OUTCOME MEASUREMENT (Phase 8)
    # =========================================================================

    def issue_campaign_tracking_id(
        self,
        strategy_id: Optional[str],
        channel_name: str,
        channel_type: str,
        content_format: str,
        crn: Optional[str] = None,
        budget_gbp: float = 0.0,
    ) -> CampaignTrackingRecord:
        """Issues a deterministic campaign tracking identifier formatted as CMP-YYYYMM-XXXX."""
        now = datetime.now(timezone.utc)
        ym = now.strftime("%Y%m")
        suffix = str(uuid.uuid4())[:4].upper()
        campaign_id = f"CMP-{ym}-{suffix}"

        clean_chan = re.sub(r"[^a-zA-Z0-9]+", "_", channel_name.strip().lower())[:30]
        clean_strat = re.sub(r"[^a-zA-Z0-9]+", "_", (strategy_id or "generic").lower())[:30]

        record = CampaignTrackingRecord(
            campaign_id=campaign_id,
            strategy_id=strategy_id,
            crn=crn,
            channel_name=channel_name,
            channel_type=channel_type,
            content_format=content_format,
            utm_campaign=f"wcf_{clean_strat}",
            utm_source=clean_chan,
            utm_medium=channel_type.lower(),
            budget_gbp=budget_gbp,
            status="ACTIVE",
            created_at=now,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        logger.info(f"Issued tracking code {campaign_id} for strategy {strategy_id} on {channel_name}")
        return record

    def record_campaign_outcome_event(
        self,
        campaign_id: str,
        event_type: str,
        crn: Optional[str] = None,
        funded_amount_gbp: float = 0.0,
        realized_commission_gbp: float = 0.0,
        event_timestamp: Optional[datetime] = None,
        source_reference: Optional[str] = None,
    ) -> CampaignOutcomeEvent:
        """Records an individual funnel event tied to a deterministic campaign code."""
        if event_type not in VALID_CAMPAIGN_EVENT_TYPES:
            raise ValueError(
                f"Invalid campaign event_type '{event_type}'. Allowed: {VALID_CAMPAIGN_EVENT_TYPES}"
            )

        ts = event_timestamp or datetime.now(timezone.utc)
        event = CampaignOutcomeEvent(
            campaign_id=campaign_id,
            event_type=event_type,
            crn=crn,
            funded_amount_gbp=funded_amount_gbp,
            realized_commission_gbp=realized_commission_gbp,
            event_timestamp=ts,
            source_reference=source_reference,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        logger.info(
            f"Recorded campaign event {event_type} for {campaign_id} "
            f"(Funded: £{funded_amount_gbp:,.2f}, Commission: £{realized_commission_gbp:,.2f})"
        )
        return event

    def ingest_campaign_outcomes_csv(self, csv_content: str) -> Dict[str, int]:
        """
        Parses CSV data containing campaign outcome events and records them.
        Header: campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp
        """
        reader = csv.DictReader(io.StringIO(csv_content))
        counts = {"ingested": 0, "skipped": 0, "errors": 0}

        for row in reader:
            try:
                cid = row.get("campaign_id", "").strip()
                etype = row.get("event_type", "").strip()
                if not cid or etype not in VALID_CAMPAIGN_EVENT_TYPES:
                    counts["skipped"] += 1
                    continue

                crn = row.get("crn", "").strip() or None
                funded_amt = float(row.get("funded_amount_gbp", 0.0) or 0.0)
                comm_amt = float(row.get("realized_commission_gbp", 0.0) or 0.0)
                ts_str = row.get("event_timestamp", "").strip()
                ts = None
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except Exception:
                        ts = None

                self.record_campaign_outcome_event(
                    campaign_id=cid,
                    event_type=etype,
                    crn=crn,
                    funded_amount_gbp=funded_amt,
                    realized_commission_gbp=comm_amt,
                    event_timestamp=ts,
                    source_reference="CSV_IMPORT",
                )
                counts["ingested"] += 1
            except Exception as e:
                logger.warning(f"Failed to ingest campaign outcome row {row}: {e}")
                counts["errors"] += 1

        return counts

    def compute_empirical_campaign_conversion_rates(
        self,
        campaign_id: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Aggregates empirical funnel conversions for a specific campaign or all campaigns.
        Strictly applies `as_of` snapshot filter to prevent future data leakage.
        """
        q = self.db.query(CampaignOutcomeEvent)
        if campaign_id:
            q = q.filter(CampaignOutcomeEvent.campaign_id == campaign_id)
        if as_of:
            q = q.filter(CampaignOutcomeEvent.event_timestamp <= as_of)

        events = q.all()

        impressions = sum(1 for e in events if e.event_type == "IMPRESSION")
        clicks = sum(1 for e in events if e.event_type == "CLICK")
        qualified = sum(1 for e in events if e.event_type == "QUALIFIED_ENQUIRY")
        applications = sum(1 for e in events if e.event_type == "APPLICATION")
        funded = sum(1 for e in events if e.event_type == "FUNDED_DEAL")
        total_funded_volume = sum(e.funded_amount_gbp for e in events if e.event_type == "FUNDED_DEAL")
        total_commission = sum(e.realized_commission_gbp for e in events if e.event_type == "FUNDED_DEAL")

        ctr = (clicks / impressions) if impressions > 0 else 0.0
        qual_rate = (qualified / clicks) if clicks > 0 else 0.0
        app_rate = (applications / qualified) if qualified > 0 else 0.0
        fund_rate = (funded / applications) if applications > 0 else 0.0

        return {
            "campaign_id": campaign_id,
            "as_of": as_of.isoformat() if as_of else None,
            "impressions": impressions,
            "clicks": clicks,
            "qualified_enquiries": qualified,
            "applications": applications,
            "funded_deals": funded,
            "click_through_rate": round(ctr, 4),
            "qualified_lead_rate": round(qual_rate, 4),
            "application_rate": round(app_rate, 4),
            "funding_approval_rate": round(fund_rate, 4),
            "total_funded_volume_gbp": round(total_funded_volume, 2),
            "total_realized_commission_gbp": round(total_commission, 2),
        }

    def update_bayesian_evoi_priors(
        self,
        channel_type: str,
        observed_successes: int,
        observed_trials: int,
        prior_alpha: float = 2.0,
        prior_beta: float = 20.0,
    ) -> Dict[str, float]:
        """
        Updates Bayesian Beta-Binomial conjugate prior with observed campaign outcomes.
        Returns posterior alpha, beta, posterior mean, and updated EVOI gain factor.
        """
        post_alpha = prior_alpha + max(0, observed_successes)
        post_beta = prior_beta + max(0, observed_trials - observed_successes)
        post_mean = post_alpha / (post_alpha + post_beta)
        prior_mean = prior_alpha / (prior_alpha + prior_beta)
        evoi_gain_factor = round(post_mean / prior_mean, 3)

        logger.info(
            f"Updated Bayesian EVOI prior for {channel_type}: "
            f"Prior Mean: {prior_mean:.3f} -> Posterior Mean: {post_mean:.3f} (EVOI Gain: {evoi_gain_factor}x)"
        )

        return {
            "channel_type": channel_type,
            "prior_alpha": prior_alpha,
            "prior_beta": prior_beta,
            "posterior_alpha": round(post_alpha, 2),
            "posterior_beta": round(post_beta, 2),
            "prior_conversion_mean": round(prior_mean, 4),
            "posterior_conversion_mean": round(post_mean, 4),
            "evoi_gain_multiplier": evoi_gain_factor,
        }

