"""
Agent H: Campaign Measurement & Outcome Learning Agent.
Issues deterministic campaign tracking identifiers, measures empirical conversion yields,
and updates Bayesian EVOI priors without future data leakage.
"""

from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.database import SessionLocal
from backend.app.services.outcome_learning import OutcomeLearningService


class CampaignMeasurementAgent(BaseAgent):
    """Measures live campaign funnel performance and updates Bayesian learning models."""

    def __init__(self, db: Optional[Session] = None):
        super().__init__(AgentRole.CAMPAIGN_MEASUREMENT)
        self._db = db

    def _get_db(self) -> Session:
        return self._db if self._db is not None else SessionLocal()

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        db = self._get_db()
        should_close = self._db is None

        try:
            service = OutcomeLearningService(db)
            action = request.input_payload.get("action", "ISSUE_CODE")

            if action == "ISSUE_CODE":
                strategy_id = request.input_payload.get("strategy_id", "STRAT-DEFAULT")
                channel_name = request.input_payload.get("channel_name", "Industry Podcast")
                channel_type = request.input_payload.get("channel_type", "PODCAST")
                content_format = request.input_payload.get("content_format", "PODCAST_SPONSORSHIP")
                budget_gbp = float(request.input_payload.get("budget_gbp", 0.0))

                record = service.issue_campaign_tracking_id(
                    strategy_id=strategy_id,
                    channel_name=channel_name,
                    channel_type=channel_type,
                    content_format=content_format,
                    crn=request.crn,
                    budget_gbp=budget_gbp,
                )
                output_payload = {
                    "action": "ISSUE_CODE",
                    "campaign_id": record.campaign_id,
                    "utm_campaign": record.utm_campaign,
                    "utm_source": record.utm_source,
                    "utm_medium": record.utm_medium,
                    "status": record.status,
                }
                evidence_refs = [
                    {
                        "source": "CAMPAIGN_TRACKING_LEDGER",
                        "ref": record.campaign_id,
                        "uri": f"urn:warden:campaign:{record.campaign_id}",
                        "excerpt": f"Issued deterministic tracking code {record.campaign_id} for {channel_name}",
                    }
                ]

            elif action == "INGEST_CSV":
                csv_content = request.input_payload.get("csv_content", "")
                result = service.ingest_campaign_outcomes_csv(csv_content)
                output_payload = {
                    "action": "INGEST_CSV",
                    "counts": result,
                }
                evidence_refs = [
                    {
                        "source": "CSV_OUTCOME_BATCH",
                        "ref": f"BATCH-{result['ingested']}",
                        "uri": "urn:warden:csv:import",
                        "excerpt": f"Ingested {result['ingested']} campaign outcome events",
                    }
                ]

            else:  # MEASURE_OUTCOMES or UPDATE_PRIORS
                cid = request.input_payload.get("campaign_id")
                as_of = request.input_payload.get("as_of")
                metrics = service.compute_empirical_campaign_conversion_rates(campaign_id=cid, as_of=as_of)
                observed_trials = max(int(metrics["applications"]), int(metrics["funded_deals"]))
                priors = service.update_bayesian_evoi_priors(
                    channel_type=request.input_payload.get("channel_type", "PODCAST"),
                    observed_successes=int(metrics["funded_deals"]),
                    observed_trials=observed_trials,
                    as_of=as_of,
                )
                output_payload = {
                    "action": "MEASURE_OUTCOMES",
                    "metrics": metrics,
                    "bayesian_evoi_update": priors,
                }
                evidence_refs = [
                    {
                        "source": "EMPIRICAL_OUTCOME_METRICS",
                        "ref": cid or "ALL_CAMPAIGNS",
                        "uri": "urn:warden:metrics:empirical",
                        "excerpt": (
                            f"Empirical funded deals: {metrics['funded_deals']}, "
                            f"Posterior conversion mean: {priors['posterior_conversion_mean']:.4f}"
                        ),
                    }
                ]

            return output_payload, evidence_refs, 0.00
        finally:
            if should_close:
                db.close()
