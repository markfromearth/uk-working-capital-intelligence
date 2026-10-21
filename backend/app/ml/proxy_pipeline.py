import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger
from backend.app.providers.registry import provider_registry
from backend.app.services.ingestion import IngestionService
from backend.app.services.tool_registry import approved_tool_registry, ApprovedToolRegistry
from backend.app.ml.dataset import DatasetBuilder, DatasetBuildResult, TARGET_NEW_CHARGE_12M


class CompaniesHouseProxyPipeline:
    """
    Historical Companies House charges backfill and proxy-label dataset construction pipeline.
    
    CRITICAL METHODOLOGICAL DISTINCTION:
    This pipeline extracts statutory charge registrations (MR01 / mortgages / debentures) to construct
    proxy labels ('new_charge_12m'). This proxy measures corporate financing DEMAND (the company sought
    and registered secured borrowing), NOT conversion with a commercial broker or funded facility acceptance.
    
    Safety & Compliance:
    1. Strictly respects tool registry rate limit bounds (default 120 queries/min).
    2. Integrates with the tool registry circuit breaker to abort on persistent upstream failure.
    3. Runs against deterministic recorded fixtures when API credentials are unconfigured or in demo mode.
    4. Never writes fabricated sales-funnel outcomes to the application database.
    """

    TOOL_NAME = "companies_house_charges_refresh"

    def __init__(self, db: Session, registry: Optional[ApprovedToolRegistry] = None):
        self.db = db
        self.registry = registry or approved_tool_registry
        self.ingestion = IngestionService(db)
        self.dataset_builder = DatasetBuilder(db)

    def backfill_charges(
        self,
        crns: List[str],
        min_interval_seconds: float = 0.5,
        max_crns: int = 100
    ) -> Dict[str, Any]:
        """
        Sequentially queries Companies House charges for target CRNs, parses MR01/MR04 filings
        into normalized signals, and respects rate limit and circuit breaker boundaries.
        """
        adapter = provider_registry.get_adapter("companies_house")
        cb = self.registry.get_circuit_breaker(self.TOOL_NAME)

        processed = 0
        signals_ingested = 0
        errors: List[Dict[str, str]] = []
        is_live = getattr(adapter, "_is_live", False) and not settings.DEMO_MODE

        logger.info(
            f"Starting Companies House charges proxy backfill for {len(crns)} CRNs. "
            f"Mode: {'LIVE API' if is_live else 'FIXTURE / SIMULATION'}. "
            f"Proxy measures: FINANCING DEMAND (new charge registration), NOT broker conversion."
        )

        for raw_crn in crns[:max_crns]:
            # Circuit breaker guard
            if not cb.can_execute():
                msg = f"Circuit breaker is OPEN for {self.TOOL_NAME}; aborting backfill."
                logger.warning(msg)
                errors.append({"crn": raw_crn, "error": msg})
                break

            norm_crn, valid, err = normalize_uk_crn(raw_crn)
            if not valid or not norm_crn:
                errors.append({"crn": raw_crn, "error": f"Invalid CRN: {err}"})
                continue

            t_start = time.time()
            try:
                charges = adapter.fetch_company_charges(norm_crn)
                crn_signals = 0
                for chg in charges:
                    parsed_signals = adapter._parse_charge_into_signals(norm_crn, chg)
                    for sig in parsed_signals:
                        sig["data_use_restrictions"] = "statutory_public_register_b2b_only"
                        self.ingestion.process_normalized_signal(raw_event_id=None, signal_data=sig)
                        signals_ingested += 1
                        crn_signals += 1

                cb.record_success()
                processed += 1

            except Exception as exc:
                cb.record_failure()
                logger.error(f"Failed charges backfill for {norm_crn}: {exc}")
                errors.append({"crn": norm_crn, "error": str(exc)})

            # Rate limit pacing: enforce minimum time interval between external queries
            elapsed = time.time() - t_start
            if is_live and elapsed < min_interval_seconds:
                time.sleep(min_interval_seconds - elapsed)

        return {
            "processed_crns": processed,
            "total_requested": len(crns),
            "signals_ingested": signals_ingested,
            "is_live_adapter": is_live,
            "circuit_breaker_open": cb.is_open,
            "errors": errors,
            "proxy_label_concept": "FINANCING_DEMAND_PROXY (MR01 registration; does not measure broker conversion)"
        }

    def build_proxy_dataset(
        self,
        crns: Optional[List[str]] = None,
        snapshot_dates: Optional[Dict[str, List[datetime]]] = None,
        horizon_days: int = 180,
        as_of_reference: Optional[datetime] = None
    ) -> DatasetBuildResult:
        """
        Constructs a leak-free point-in-time dataset using the statutory charge demand proxy.
        Label = 1 if a new charge (MR01 / charge registration) occurs within horizon_days after snapshot.
        """
        return self.dataset_builder.build_dataset(
            crns=crns,
            snapshot_dates=snapshot_dates,
            target=TARGET_NEW_CHARGE_12M,
            horizon_days=horizon_days,
            as_of_reference=as_of_reference
        )
