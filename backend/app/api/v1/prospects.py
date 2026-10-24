from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from backend.app.database import get_db
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.models.signal import NormalizedSignal
from backend.app.models.score import LiquidityScoreRecord
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.review import BrokerReviewAction
from backend.app.providers.registry import provider_registry
from backend.app.schemas.review import ProspectListItem
from backend.app.services.pipeline_orchestrator import default_pipeline_orchestrator

router = APIRouter(prefix="/prospects", tags=["Prospect Intelligence"])

@router.get("/dashboard-metrics")
def get_dashboard_metrics(db: Session = Depends(get_db)):
    """Summary metrics for the commercial broker intelligence dashboard."""
    now = datetime.now(timezone.utc)
    t_30d = now - timedelta(days=30)

    total_companies = db.query(func.count(CompanyRegistry.crn)).scalar() or 0
    signals_30d = db.query(func.count(NormalizedSignal.signal_id)).filter(
        NormalizedSignal.observation_timestamp >= t_30d
    ).scalar() or 0

    # Get latest score for each company
    latest_scores_subq = db.query(
        LiquidityScoreRecord.crn,
        func.max(LiquidityScoreRecord.scored_at).label("max_scored_at")
    ).group_by(LiquidityScoreRecord.crn).subquery()

    latest_scores = db.query(LiquidityScoreRecord).join(
        latest_scores_subq,
        (LiquidityScoreRecord.crn == latest_scores_subq.c.crn) &
        (LiquidityScoreRecord.scored_at == latest_scores_subq.c.max_scored_at)
    ).all()

    elevated_count = sum(1 for s in latest_scores if s.score >= 50.0)
    strong_count = sum(1 for s in latest_scores if s.score >= 75.0)

    # High-priority awaiting review
    reviewed_crns = {r.crn for r in db.query(BrokerReviewAction.crn).filter(
        BrokerReviewAction.review_status.in_(["in_review", "verified_opportunity", "suppressed", "snoozed"])
    ).all()}
    
    high_priority_awaiting_review = sum(1 for s in latest_scores if s.score >= 50.0 and s.crn not in reviewed_crns)

    # Unresolved entity matches in review queue
    unresolved_matches = db.query(func.count(CompanyMatchReview.id)).filter(
        CompanyMatchReview.status == "pending"
    ).scalar() or 0

    # Provider health summary
    provider_health = [p.model_dump() for p in provider_registry.get_health_summary()]

    # Band breakdown
    band_distribution = {
        "strong": sum(1 for s in latest_scores if s.score_band == "strong"),
        "elevated": sum(1 for s in latest_scores if s.score_band == "elevated"),
        "emerging": sum(1 for s in latest_scores if s.score_band == "emerging"),
        "limited": sum(1 for s in latest_scores if s.score_band == "limited")
    }

    # Qualified dossiers in memory store
    qualified_count = len(default_pipeline_orchestrator._dossier_store)
    total_unlocked = sum(
        d.trapped_cash.estimated_facility_size
        for d in default_pipeline_orchestrator._dossier_store.values()
        if d.trapped_cash and d.trapped_cash.estimated_facility_size
    )

    return {
        "total_monitored_companies": total_companies,
        "newly_detected_signals_30d": signals_30d,
        "elevated_prospects_count": elevated_count,
        "strong_prospects_count": strong_count,
        "high_priority_awaiting_review": high_priority_awaiting_review,
        "unresolved_entity_matches": unresolved_matches,
        "total_qualified_prospects": qualified_count,
        "total_trapped_cash_unlocked": total_unlocked,
        "band_distribution": band_distribution,
        "provider_integrations": provider_health,
        "is_demo_mode": any(db.query(CompanyRegistry.is_synthetic).filter(CompanyRegistry.is_synthetic == 1).first() or [False])
    }

@router.get("", response_model=List[ProspectListItem])
def list_prioritized_prospects(
    score_band: Optional[str] = None,
    review_status: Optional[str] = None,
    evidence_coverage: Optional[str] = None,
    min_score: Optional[float] = None,
    search: Optional[str] = None,
    sort_by: str = "score",  # 'score', 'recent', 'name'
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Returns prioritized prospects with rich filter criteria, latest signals,
    and assigned broker review status.
    """
    companies = db.query(CompanyRegistry).filter(CompanyRegistry.is_synthetic == 0).all()
    prospects = []

    for comp in companies:
        if search:
            q = search.lower()
            if q not in comp.company_name.lower() and q not in comp.crn.lower():
                continue

        # Latest score
        score_rec = db.query(LiquidityScoreRecord).filter(
            LiquidityScoreRecord.crn == comp.crn
        ).order_by(LiquidityScoreRecord.scored_at.desc()).first()

        score_val = score_rec.score if score_rec else 0.0
        band = score_rec.score_band if score_rec else "limited"
        coverage = score_rec.evidence_coverage if score_rec else "limited"

        if score_band and band != score_band:
            continue
        if min_score is not None and score_val < min_score:
            continue
        if evidence_coverage and coverage != evidence_coverage:
            continue

        # Review status
        review = db.query(BrokerReviewAction).filter(
            BrokerReviewAction.crn == comp.crn
        ).order_by(BrokerReviewAction.created_at.desc()).first()

        current_review_status = review.review_status if review else "unreviewed"
        assigned_broker = review.assigned_broker_name if review else None

        if review_status and current_review_status != review_status:
            continue

        # Latest signals
        latest_sig = db.query(NormalizedSignal).filter(
            NormalizedSignal.crn == comp.crn
        ).order_by(NormalizedSignal.observation_timestamp.desc()).first()

        # Build primary signal summary text
        signals = db.query(NormalizedSignal).filter(
            NormalizedSignal.crn == comp.crn
        ).order_by(NormalizedSignal.observation_timestamp.desc()).limit(3).all()
        
        sig_summary_parts = []
        for s in signals:
            if s.signal_type == "charge_satisfaction_mr04":
                sig_summary_parts.append("MR04 Bank Release")
            elif s.signal_type == "officer_resignation_tm01":
                sig_summary_parts.append("TM01 Officer Cluster")
            elif s.signal_type == "accounts_balance_sheet_extracted":
                sig_summary_parts.append("Trapped Cash Sized")
            elif s.signal_type == "gazette_notice_gaz1":
                sig_summary_parts.append("Gazette Notice")
            elif s.signal_type == "new_charge_registered":
                sig_summary_parts.append("Debenture Registered")
            elif s.signal_type == "dbt_increase":
                sig_summary_parts.append(f"DBT {s.value_numeric:.0f}d")
            elif s.signal_type == "headcount_surge":
                sig_summary_parts.append(f"+{s.value_numeric:.0f}% Headcount")
            elif s.signal_type == "credit_score_drop":
                sig_summary_parts.append("Credit Drop")

        primary_summary = " + ".join(sig_summary_parts) if sig_summary_parts else "Statutory monitoring"

        dossier = default_pipeline_orchestrator.get_dossier(comp.crn)
        facility_val = dossier.trapped_cash.estimated_facility_size if dossier and dossier.trapped_cash else None
        ch_url = f"https://find-and-update.company-information.service.gov.uk/company/{comp.crn}"

        prospects.append(ProspectListItem(
            crn=comp.crn,
            company_name=comp.company_name,
            company_status=comp.company_status or "active",
            liquidity_strain_score=score_val,
            score_band=band,
            score_change_90d=0.0,
            primary_signals_summary=primary_summary,
            latest_signal_date=latest_sig.observation_timestamp if latest_sig else None,
            evidence_coverage=coverage,
            review_status=current_review_status,
            assigned_broker_name=assigned_broker,
            last_updated=score_rec.scored_at if score_rec else comp.created_at,
            is_synthetic=comp.is_synthetic or 0,
            trapped_cash_facility=facility_val,
            primary_source_url=ch_url
        ))

    # Sorting
    if sort_by == "score":
        prospects.sort(key=lambda x: x.liquidity_strain_score, reverse=True)
    elif sort_by == "recent":
        prospects.sort(key=lambda x: x.latest_signal_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    elif sort_by == "name":
        prospects.sort(key=lambda x: x.company_name)

    return prospects[skip : skip + limit]
