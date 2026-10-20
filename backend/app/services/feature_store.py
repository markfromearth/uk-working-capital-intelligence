import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.signal import NormalizedSignal
from backend.app.models.feature import CompanyFeatureSnapshot

class FeatureStoreService:
    """
    Computes Point-in-Time correct feature snapshots for UK companies.
    Guarantees no future leakage: only signals where observation_timestamp <= as_of_timestamp are used.
    """

    def __init__(self, db: Session):
        self.db = db

    def compute_feature_snapshot(
        self,
        crn: str,
        as_of_timestamp: Optional[datetime] = None
    ) -> CompanyFeatureSnapshot:
        if as_of_timestamp is None:
            as_of_timestamp = datetime.now(timezone.utc)
        elif as_of_timestamp.tzinfo is None:
            as_of_timestamp = as_of_timestamp.replace(tzinfo=timezone.utc)

        # Retrieve strictly past/present signals for this company relative to as_of_timestamp
        signals: List[NormalizedSignal] = self.db.query(NormalizedSignal).filter(
            NormalizedSignal.crn == crn,
            NormalizedSignal.observation_timestamp <= as_of_timestamp
        ).order_by(NormalizedSignal.observation_timestamp.desc()).all()

        # Window thresholds
        t_30d = as_of_timestamp - timedelta(days=30)
        t_90d = as_of_timestamp - timedelta(days=90)
        t_180d = as_of_timestamp - timedelta(days=180)

        # 1. Financing & Charge features
        chg_30d = 0
        chg_90d = 0
        chg_180d = 0
        chg_created_90d = 0
        latest_sat_age_days: Optional[float] = None
        latest_scope: Optional[str] = None
        charge_strength = 0.0

        # 2. Credit & Payment Risk (DBT vs DSO strictly separate)
        dbt_current: Optional[float] = None
        dbt_trend_90d: Optional[float] = None
        credit_score_current: Optional[float] = None
        credit_score_drop_90d: Optional[float] = None
        adverse_count = 0
        
        # DSO (strictly segregated, never fabricated from DBT)
        dso_current: Optional[float] = None
        dso_trend_90d: Optional[float] = None
        dso_available = 0

        # 3. Workforce & Headcount Dynamics
        headcount_current: Optional[int] = None
        growth_30d_pct: Optional[float] = None
        growth_90d_pct: Optional[float] = None
        growth_180d_pct: Optional[float] = None
        acceleration_pct: Optional[float] = None
        hc_obs_count = 0
        hc_confidence = 0.0

        # 4. Corroboration & Metadata
        distinct_sources = set()
        strain_signals = []
        contradictory_flags = []

        for sig in signals:
            obs = sig.observation_timestamp
            if obs.tzinfo is None:
                obs = obs.replace(tzinfo=timezone.utc)
            
            distinct_sources.add(sig.source_provider)
            if sig.direction == "strain_increasing":
                strain_signals.append(sig)
            elif sig.direction == "strain_decreasing":
                contradictory_flags.append(f"{sig.signal_type}: {sig.value_text}")

            # Charge satisfaction MR04
            if sig.signal_type == "charge_satisfaction_mr04":
                age_days = (as_of_timestamp - obs).total_seconds() / 86400.0
                if latest_sat_age_days is None or age_days < latest_sat_age_days:
                    latest_sat_age_days = age_days
                    # determine scope
                    if sig.metadata_json:
                        try:
                            meta = json.loads(sig.metadata_json)
                            latest_scope = meta.get("scope", "full")
                        except Exception:
                            latest_scope = "full"
                    else:
                        latest_scope = "full"

                if obs >= t_30d:
                    chg_30d += 1
                if obs >= t_90d:
                    chg_90d += 1
                if obs >= t_180d:
                    chg_180d += 1
                
                charge_strength = max(charge_strength, sig.confidence_score or 0.8)

            elif sig.signal_type == "charge_creation" and obs >= t_90d:
                chg_created_90d += 1

            # Credit & DBT
            elif sig.signal_type == "dbt_increase":
                if dbt_current is None:
                    dbt_current = sig.value_numeric
                if sig.metadata_json and dbt_trend_90d is None:
                    try:
                        meta = json.loads(sig.metadata_json)
                        dbt_trend_90d = meta.get("dbt_change_days")
                    except Exception:
                        pass

            elif sig.signal_type == "credit_score_drop":
                if credit_score_drop_90d is None:
                    credit_score_drop_90d = sig.value_numeric
                if sig.metadata_json and credit_score_current is None:
                    try:
                        meta = json.loads(sig.metadata_json)
                        credit_score_current = meta.get("current_score")
                    except Exception:
                        pass

            # DSO - only populated when genuine DSO signal exists
            elif sig.signal_type == "dso_increase":
                dso_available = 1
                if dso_current is None:
                    dso_current = sig.value_numeric
                if sig.metadata_json and dso_trend_90d is None:
                    try:
                        meta = json.loads(sig.metadata_json)
                        dso_trend_90d = meta.get("dso_change_days")
                    except Exception:
                        pass

            # Workforce
            elif sig.signal_type == "headcount_surge":
                if growth_90d_pct is None:
                    growth_90d_pct = sig.value_numeric
                    hc_confidence = sig.confidence_score or 0.8
                if sig.metadata_json:
                    try:
                        meta = json.loads(sig.metadata_json)
                        if headcount_current is None:
                            headcount_current = meta.get("current_headcount")
                        if growth_30d_pct is None:
                            growth_30d_pct = meta.get("growth_30d_pct")
                        if acceleration_pct is None:
                            acceleration_pct = meta.get("acceleration_pct")
                        hc_obs_count = meta.get("observation_count", 1)
                    except Exception:
                        pass

        # Determine evidence coverage category
        corroboration_count = len(strain_signals)
        source_count = len(distinct_sources)
        if source_count >= 3 and corroboration_count >= 3:
            coverage = "strong"
        elif source_count >= 2 and corroboration_count >= 2:
            coverage = "elevated"
        elif corroboration_count >= 1:
            coverage = "emerging"
        else:
            coverage = "limited"

        snapshot = CompanyFeatureSnapshot(
            crn=crn,
            as_of_timestamp=as_of_timestamp,
            charges_satisfied_30d=chg_30d,
            charges_satisfied_90d=chg_90d,
            charges_satisfied_180d=chg_180d,
            charges_created_90d=chg_created_90d,
            age_latest_charge_satisfaction_days=round(latest_sat_age_days, 1) if latest_sat_age_days is not None else None,
            latest_charge_scope=latest_scope,
            charge_evidence_strength=round(charge_strength, 2),
            dbt_current_days=round(dbt_current, 1) if dbt_current is not None else None,
            dbt_trend_90d=round(dbt_trend_90d, 1) if dbt_trend_90d is not None else None,
            credit_score_current=round(credit_score_current, 1) if credit_score_current is not None else None,
            credit_score_drop_90d=round(credit_score_drop_90d, 1) if credit_score_drop_90d is not None else None,
            adverse_insolvency_events_count=adverse_count,
            dso_current_days=round(dso_current, 1) if dso_current is not None else None,
            dso_trend_90d=round(dso_trend_90d, 1) if dso_trend_90d is not None else None,
            dso_data_available=dso_available,
            headcount_current=headcount_current,
            headcount_growth_30d_pct=round(growth_30d_pct, 1) if growth_30d_pct is not None else None,
            headcount_growth_90d_pct=round(growth_90d_pct, 1) if growth_90d_pct is not None else None,
            headcount_growth_180d_pct=round(growth_180d_pct, 1) if growth_180d_pct is not None else None,
            headcount_acceleration_pct=round(acceleration_pct, 1) if acceleration_pct is not None else None,
            headcount_observation_count=hc_obs_count,
            headcount_confidence_score=round(hc_confidence, 2),
            corroboration_signal_count=corroboration_count,
            distinct_source_count=source_count,
            evidence_coverage=coverage,
            contradictory_evidence_flags=json.dumps(contradictory_flags) if contradictory_flags else None
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot
