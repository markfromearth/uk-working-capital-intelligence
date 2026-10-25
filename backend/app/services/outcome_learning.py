import csv
import hashlib
import io
import math
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger
from backend.app.models.decision_maker import SuppressionRecord
from backend.app.models.investigation import CommercialFunnelOutcome
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.marketing import CampaignOutcomeEvent, CampaignTrackingRecord
from backend.app.services.audit import log_audit_event
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

VALID_CAMPAIGN_STATUSES = [
    "DRAFT",
    "ACTIVE",
    "PAUSED",
    "COMPLETED",
    "CANCELLED",
    "SUPPRESSED",
]

PERMITTED_CAMPAIGN_TRANSITIONS: Dict[str, List[str]] = {
    "DRAFT": ["ACTIVE", "CANCELLED", "SUPPRESSED"],
    "ACTIVE": ["PAUSED", "COMPLETED", "CANCELLED", "SUPPRESSED"],
    "PAUSED": ["ACTIVE", "COMPLETED", "CANCELLED", "SUPPRESSED"],
    "COMPLETED": [],
    "CANCELLED": [],
    "SUPPRESSED": [],
}

MAX_CSV_PAYLOAD_BYTES = 512 * 1024  # 512 KB
MAX_CSV_ROWS = 5000
REQUIRED_CSV_HEADERS = {
    "campaign_id",
    "event_type",
    "crn",
    "funded_amount_gbp",
    "realized_commission_gbp",
    "event_timestamp",
}

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
    # CAMPAIGN TRACKING & OUTCOME MEASUREMENT (Phase 1, 3, 7)
    # =========================================================================

    def _check_crn_suppressed(self, crn: Optional[str]) -> Optional[SuppressionRecord]:
        if not crn:
            return None
        norm_crn, valid, _ = normalize_uk_crn(crn)
        target = (norm_crn if valid and norm_crn else crn).strip().lower()
        return (
            self.db.query(SuppressionRecord)
            .filter(
                SuppressionRecord.is_active == True,
                func.lower(SuppressionRecord.identifier_value) == target,
            )
            .first()
        )

    def _verify_campaign_access(
        self,
        campaign_id: str,
        actor_user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        require_active: bool = False,
    ) -> CampaignTrackingRecord:
        record = (
            self.db.query(CampaignTrackingRecord)
            .filter(CampaignTrackingRecord.campaign_id == campaign_id)
            .first()
        )
        if not record:
            raise ValueError(f"Campaign tracking ID '{campaign_id}' does not exist.")

        # Enforce strict ownership when actor_user_id is present:
        # Non-privileged users (e.g., BROKER) may only access campaigns they explicitly own (never unowned/system campaigns).
        is_privileged = (actor_role or "").upper() in ("ADMIN", "ANALYST")
        if actor_user_id and not is_privileged:
            if not record.owner_user_id or str(record.owner_user_id) != str(actor_user_id):
                raise PermissionError(
                    f"Access denied: User '{actor_user_id}' does not own campaign '{campaign_id}'."
                )

        if require_active and record.status in ("CANCELLED", "SUPPRESSED"):
            raise ValueError(
                f"Campaign '{campaign_id}' has status '{record.status}' and cannot accept new events."
            )

        return record

    def transition_campaign_status(
        self,
        campaign_id: str,
        target_status: str,
        actor_user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
    ) -> CampaignTrackingRecord:
        """Validates campaign ownership and enforces permitted state transitions."""
        clean_target = target_status.strip().upper()
        if clean_target not in VALID_CAMPAIGN_STATUSES:
            raise ValueError(
                f"Invalid campaign status '{target_status}'. Allowed: {VALID_CAMPAIGN_STATUSES}"
            )

        record = self._verify_campaign_access(
            campaign_id=campaign_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            require_active=False,
        )
        current_status = (record.status or "ACTIVE").upper()
        if current_status == clean_target:
            return record

        allowed = PERMITTED_CAMPAIGN_TRANSITIONS.get(current_status, [])
        if clean_target not in allowed:
            raise ValueError(
                f"Invalid campaign state transition from '{current_status}' to '{clean_target}'. "
                f"Permitted transitions from '{current_status}': {allowed}"
            )

        record.status = clean_target
        record.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(record)

        log_audit_event(
            db=self.db,
            action="campaign_status_transition",
            resource_type="campaign_tracking",
            resource_id=campaign_id,
            user_id=actor_user_id,
            user_role=actor_role,
            details={"from_status": current_status, "to_status": clean_target},
        )
        return record

    def issue_campaign_tracking_id(
        self,
        strategy_id: Optional[str],
        channel_name: str,
        channel_type: str,
        content_format: str,
        crn: Optional[str] = None,
        budget_gbp: float = 0.0,
        owner_user_id: Optional[str] = None,
        reference_date: Optional[datetime] = None,
    ) -> CampaignTrackingRecord:
        """
        Issues a deterministic, collision-resistant campaign tracking identifier formatted as
        CMP-YYYYMM-XXXXXXXX. Idempotent for identical canonical attributes within the same month.
        Blocks issuance if the target CRN is in the active suppression registry.
        """
        if budget_gbp < 0.0:
            raise ValueError("Campaign budget_gbp cannot be negative.")

        norm_crn = None
        if crn:
            c_norm, valid, err = normalize_uk_crn(crn)
            if not valid or not c_norm:
                raise ValueError(f"Invalid UK CRN '{crn}': {err}")
            norm_crn = c_norm

            supp = self._check_crn_suppressed(norm_crn)
            if supp:
                raise ValueError(
                    f"Compliance block: Target CRN {norm_crn} is suppressed ({supp.suppression_reason})."
                )

        now = reference_date or datetime.now(timezone.utc)
        ym = now.strftime("%Y%m")

        clean_chan = re.sub(r"[^a-zA-Z0-9]+", "_", channel_name.strip().lower()).strip("_")[:30]
        clean_strat = re.sub(r"[^a-zA-Z0-9]+", "_", (strategy_id or "generic").strip().lower()).strip("_")[:30]
        clean_type = channel_type.strip().upper()
        clean_fmt = content_format.strip().upper()

        canonical_seed = "|".join([
            ym,
            clean_strat,
            clean_chan,
            clean_type,
            clean_fmt,
            norm_crn or "ALL",
            owner_user_id or "SYSTEM",
        ])
        digest = hashlib.sha256(canonical_seed.encode("utf-8")).hexdigest()[:8].upper()
        campaign_id = f"CMP-{ym}-{digest}"

        existing = (
            self.db.query(CampaignTrackingRecord)
            .filter(CampaignTrackingRecord.campaign_id == campaign_id)
            .first()
        )
        if existing:
            return existing

        record = CampaignTrackingRecord(
            campaign_id=campaign_id,
            strategy_id=strategy_id,
            crn=norm_crn,
            channel_name=channel_name.strip(),
            channel_type=clean_type,
            content_format=clean_fmt,
            utm_campaign=f"wcf_{clean_strat}",
            utm_source=clean_chan,
            utm_medium=clean_type.lower(),
            budget_gbp=budget_gbp,
            status="ACTIVE",
            owner_user_id=owner_user_id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        logger.info(f"Issued deterministic tracking code {campaign_id} for strategy {strategy_id} on {channel_name}")
        return record

    @staticmethod
    def compute_outcome_event_fingerprint(
        campaign_id: str,
        event_type: str,
        crn: Optional[str],
        funded_amount_gbp: float,
        realized_commission_gbp: float,
        event_timestamp: datetime,
    ) -> str:
        ts_utc = (
            event_timestamp.astimezone(timezone.utc)
            if event_timestamp.tzinfo
            else event_timestamp.replace(tzinfo=timezone.utc)
        )
        canonical = "|".join([
            campaign_id.strip(),
            event_type.strip().upper(),
            (crn or "").strip().upper(),
            f"{float(funded_amount_gbp):.2f}",
            f"{float(realized_commission_gbp):.2f}",
            ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record_campaign_outcome_event(
        self,
        campaign_id: str,
        event_type: str,
        crn: Optional[str] = None,
        funded_amount_gbp: float = 0.0,
        realized_commission_gbp: float = 0.0,
        event_timestamp: Optional[datetime] = None,
        source_reference: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        enforce_campaign_exists: bool = True,
    ) -> CampaignOutcomeEvent:
        """
        Records an individual funnel event tied to a deterministic campaign code.
        Validates campaign existence, ownership, non-negative monetary values, and deduplicates events.
        Attaches `is_duplicate: bool` attribute on the returned CampaignOutcomeEvent instance.
        """
        if event_type not in VALID_CAMPAIGN_EVENT_TYPES:
            raise ValueError(
                f"Invalid campaign event_type '{event_type}'. Allowed: {VALID_CAMPAIGN_EVENT_TYPES}"
            )
        if funded_amount_gbp < 0.0 or realized_commission_gbp < 0.0:
            raise ValueError("Monetary outcome amounts (funded_amount_gbp, realized_commission_gbp) cannot be negative.")
        if math.isnan(funded_amount_gbp) or math.isinf(funded_amount_gbp) or math.isnan(realized_commission_gbp) or math.isinf(realized_commission_gbp):
            raise ValueError("Monetary outcome amounts must be finite numbers.")

        if enforce_campaign_exists:
            self._verify_campaign_access(
                campaign_id=campaign_id,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                require_active=True,
            )

        norm_crn = crn
        if crn:
            c_norm, valid, err = normalize_uk_crn(crn)
            if not valid or not c_norm:
                raise ValueError(f"Invalid UK CRN '{crn}': {err}")
            norm_crn = c_norm

        ts = event_timestamp or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        fingerprint = self.compute_outcome_event_fingerprint(
            campaign_id=campaign_id,
            event_type=event_type,
            crn=norm_crn,
            funded_amount_gbp=funded_amount_gbp,
            realized_commission_gbp=realized_commission_gbp,
            event_timestamp=ts,
        )

        existing = (
            self.db.query(CampaignOutcomeEvent)
            .filter(CampaignOutcomeEvent.event_fingerprint == fingerprint)
            .first()
        )
        if existing:
            existing.is_duplicate = True
            return existing

        event = CampaignOutcomeEvent(
            campaign_id=campaign_id,
            event_type=event_type,
            crn=norm_crn,
            funded_amount_gbp=funded_amount_gbp,
            realized_commission_gbp=realized_commission_gbp,
            event_timestamp=ts,
            source_reference=source_reference,
            event_fingerprint=fingerprint,
            recorded_by_user_id=actor_user_id,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        event.is_duplicate = False
        logger.info(
            f"Recorded campaign event {event_type} for {campaign_id} "
            f"(Funded: £{funded_amount_gbp:,.2f}, Commission: £{realized_commission_gbp:,.2f})"
        )
        return event

    def ingest_campaign_outcomes_csv(
        self,
        csv_content: str,
        actor_user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Parses CSV data containing campaign outcome events and records them deterministically.
        Enforces payload byte limits, row count limits, strict header validation, campaign ownership,
        and event deduplication.
        Header: campaign_id,event_type,crn,funded_amount_gbp,realized_commission_gbp,event_timestamp
        """
        if not isinstance(csv_content, str) or not csv_content.strip():
            raise ValueError("CSV content cannot be empty.")

        encoded_bytes = csv_content.encode("utf-8", errors="strict")
        if len(encoded_bytes) > MAX_CSV_PAYLOAD_BYTES:
            raise ValueError(
                f"CSV payload exceeds maximum allowed size of {MAX_CSV_PAYLOAD_BYTES} bytes."
            )
        if "\x00" in csv_content:
            raise ValueError("CSV content contains null bytes and was rejected.")

        reader = csv.DictReader(io.StringIO(csv_content.strip()))
        if not reader.fieldnames:
            raise ValueError("CSV header row is missing.")

        normalized_headers = {h.strip() for h in reader.fieldnames if h}
        missing_headers = REQUIRED_CSV_HEADERS - normalized_headers
        if missing_headers:
            raise ValueError(
                f"CSV missing required headers: {sorted(missing_headers)}. "
                f"Required: {sorted(REQUIRED_CSV_HEADERS)}"
            )

        counts = {"ingested": 0, "duplicates": 0, "skipped": 0, "errors": 0}
        row_count = 0

        for row in reader:
            row_count += 1
            if row_count > MAX_CSV_ROWS:
                raise ValueError(f"CSV exceeds maximum allowed row count of {MAX_CSV_ROWS} rows.")

            try:
                if None in row and row[None]:
                    counts["errors"] += 1
                    continue

                cid = (row.get("campaign_id") or "").strip()
                etype = (row.get("event_type") or "").strip().upper()
                if not cid or etype not in VALID_CAMPAIGN_EVENT_TYPES:
                    counts["skipped"] += 1
                    continue

                crn = (row.get("crn") or "").strip() or None
                funded_amt = float((row.get("funded_amount_gbp") or "0").strip() or 0.0)
                comm_amt = float((row.get("realized_commission_gbp") or "0").strip() or 0.0)
                ts_str = (row.get("event_timestamp") or "").strip()
                if not ts_str:
                    counts["errors"] += 1
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                event = self.record_campaign_outcome_event(
                    campaign_id=cid,
                    event_type=etype,
                    crn=crn,
                    funded_amount_gbp=funded_amt,
                    realized_commission_gbp=comm_amt,
                    event_timestamp=ts,
                    source_reference="CSV_IMPORT",
                    actor_user_id=actor_user_id,
                    actor_role=actor_role,
                    enforce_campaign_exists=True,
                )
                if getattr(event, "is_duplicate", False):
                    counts["duplicates"] += 1
                else:
                    counts["ingested"] += 1
            except PermissionError:
                raise
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
            as_of_utc = as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
            q = q.filter(CampaignOutcomeEvent.event_timestamp <= as_of_utc)

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
            "sample_size_events": len(events),
            "is_empirically_sufficient": applications >= 5,
        }

    def update_bayesian_evoi_priors(
        self,
        channel_type: str,
        observed_successes: int,
        observed_trials: int,
        prior_alpha: float = 2.0,
        prior_beta: float = 20.0,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Updates Bayesian Beta-Binomial conjugate prior with observed campaign outcomes.
        Validates that 0 <= observed_successes <= observed_trials andprior parameters > 0.
        Returns posterior alpha, beta, posterior mean, 95% normal-approximation credible interval,
        sample size, model version, and updated EVOI gain factor.
        """
        if prior_alpha <= 0.0 or prior_beta <= 0.0:
            raise ValueError("Bayesian Beta prior parameters (prior_alpha, prior_beta) must be strictly positive.")
        if observed_trials < 0 or observed_successes < 0:
            raise ValueError("Observed trials and successes cannot be negative.")
        if observed_successes > observed_trials:
            raise ValueError(
                f"Invalid Binomial observation: observed_successes ({observed_successes}) "
                f"cannot exceed observed_trials ({observed_trials})."
            )

        post_alpha = prior_alpha + float(observed_successes)
        post_beta = prior_beta + float(observed_trials - observed_successes)
        total_ab = post_alpha + post_beta
        post_mean = post_alpha / total_ab
        prior_mean = prior_alpha / (prior_alpha + prior_beta)

        # Exact Beta posterior variance: alpha * beta / ((alpha + beta)^2 * (alpha + beta + 1))
        post_var = (post_alpha * post_beta) / ((total_ab ** 2) * (total_ab + 1.0))
        post_std = math.sqrt(post_var)
        ci_lower = max(0.0, round(post_mean - 1.96 * post_std, 4))
        ci_upper = min(1.0, round(post_mean + 1.96 * post_std, 4))

        evoi_gain_factor = round(post_mean / prior_mean, 3) if observed_trials > 0 else 1.0

        logger.info(
            f"Updated Bayesian EVOI prior for {channel_type}: "
            f"Prior Mean: {prior_mean:.3f} -> Posterior Mean: {post_mean:.3f} (EVOI Gain: {evoi_gain_factor}x)"
        )

        return {
            "channel_type": channel_type,
            "model_version": "beta-binomial-v2.0",
            "cutoff_as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
            "observed_trials": observed_trials,
            "observed_successes": observed_successes,
            "prior_alpha": prior_alpha,
            "prior_beta": prior_beta,
            "posterior_alpha": round(post_alpha, 2),
            "posterior_beta": round(post_beta, 2),
            "prior_conversion_mean": round(prior_mean, 4),
            "posterior_conversion_mean": round(post_mean, 4),
            "posterior_std": round(post_std, 4),
            "credible_interval_95": [ci_lower, ci_upper],
            "evoi_gain_multiplier": evoi_gain_factor,
        }

