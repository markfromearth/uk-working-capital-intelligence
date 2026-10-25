"""
FastAPI REST API router for B2B Marketing Intelligence & Commercial Activation.
Exposes marketing channel discovery, strategy generation, full-funnel economics calculations,
deterministic campaign tracking, and empirical outcome learning.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.v1.auth import get_current_user, require_role
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.database import get_db
from backend.app.models.marketing import CampaignOutcomeEvent, CampaignTrackingRecord
from backend.app.models.user import User
from backend.app.schemas.marketing import CampaignEconomicsModel, CampaignStrategy
from backend.app.services.campaign_economics import CampaignEconomicsService
from backend.app.services.campaign_strategy import CampaignStrategyService
from backend.app.services.marketing_discovery import MarketingChannelDiscoveryService
from backend.app.services.outcome_learning import OutcomeLearningService

router = APIRouter(prefix="/marketing", tags=["Marketing Intelligence & Activation"])

ALL_AUTHENTICATED_ROLES = ["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]


# --- Schemas ---

class GenerateStrategiesRequest(BaseModel):
    company_name: Optional[str] = None
    facility_size_gbp: float = Field(default=350_000.0, ge=1_000.0)
    triggered_indicators: List[str] = Field(
        default_factory=lambda: ["contract_award_growth", "white_space"]
    )
    sic_codes: Optional[List[str]] = None
    has_active_charges: bool = False
    active_charge_count: int = Field(default=0, ge=0)
    verification_status: str = "VERIFIED"


class PlanEconomicsRequest(BaseModel):
    facility_size_gbp: float = Field(default=350_000.0, ge=1_000.0)
    campaign_cost_gbp: float = Field(default=0.0, ge=0.0)
    target_reach: int = Field(default=1000, ge=10)
    strategy_id: Optional[str] = None
    is_historical_benchmark: bool = False
    observed_rates: Optional[Dict[str, float]] = None


class TrackCampaignRequest(BaseModel):
    strategy_id: Optional[str] = None
    channel_name: str = Field(..., min_length=1, max_length=255)
    channel_type: str = Field(..., min_length=1, max_length=50)
    content_format: str = Field(..., min_length=1, max_length=50)
    crn: Optional[str] = None
    budget_gbp: float = Field(default=0.0, ge=0.0, le=1_000_000.0)


class CampaignStatusTransitionRequest(BaseModel):
    target_status: str = Field(..., description="DRAFT, ACTIVE, PAUSED, COMPLETED, CANCELLED, SUPPRESSED")


class IngestOutcomeEventRequest(BaseModel):
    campaign_id: str = Field(..., min_length=4, max_length=50)
    event_type: str = Field(..., min_length=3, max_length=50)
    crn: Optional[str] = None
    funded_amount_gbp: float = Field(default=0.0, ge=0.0)
    realized_commission_gbp: float = Field(default=0.0, ge=0.0)
    event_timestamp: Optional[datetime] = None
    source_reference: Optional[str] = None


class IngestOutcomeCsvRequest(BaseModel):
    csv_content: str = Field(..., min_length=1, max_length=524_288)


# --- Endpoints ---

@router.get("/channels")
def list_channel_candidates(
    channel_type: Optional[str] = None,
    sic_prefix: Optional[str] = None,
    sector_keyword: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """
    Returns verified B2B marketing channel candidates across UK podcasts,
    trade associations, industry publications, and events.
    """
    service = MarketingChannelDiscoveryService(db)
    sic_list = [sic_prefix] if sic_prefix else None
    results = service.discover_channels(
        sic_codes=sic_list,
        sector_keyword=sector_keyword,
        channel_type=channel_type,
        limit=limit,
    )
    return {
        "count": len(results),
        "channels": [
            {
                "id": c.id,
                "channel_name": c.channel_name,
                "channel_type": c.channel_type,
                "sector_sic_prefixes": c.sector_sic_prefixes,
                "website_uri": c.website_uri,
                "feed_uri": c.feed_uri,
                "reach_metric_text": c.reach_metric_text,
                "is_reach_verified": c.is_reach_verified,
                "commercial_opportunity_type": c.commercial_opportunity_type,
                "estimated_cost_tier": c.estimated_cost_tier,
                "confidence_score": c.confidence_score,
                "source_reference": c.source_reference,
            }
            for c in results
        ],
    }


@router.post("/strategies/generate/{crn}")
def generate_campaign_strategies(
    crn: str,
    payload: Optional[GenerateStrategiesRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """
    Generates differentiated, evidence-backed commercial finance campaign briefs,
    creative angles, and validation test plans for a target company.
    """
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    req = payload or GenerateStrategiesRequest()
    company_name = req.company_name or f"Company {norm_crn}"

    # Discover relevant channels for context
    discovery_service = MarketingChannelDiscoveryService(db)
    channels = discovery_service.discover_channels(sic_codes=req.sic_codes, limit=5)
    channel_dicts = [
        {
            "channel_name": c.channel_name,
            "channel_type": c.channel_type,
            "website_uri": c.website_uri,
        }
        for c in channels
    ]

    strategy_service = CampaignStrategyService()
    strategies = strategy_service.generate_strategies(
        company_name=company_name,
        crn=norm_crn,
        triggered_indicators=req.triggered_indicators,
        indicative_facility_gbp=req.facility_size_gbp,
        available_channels=channel_dicts,
        has_active_charges=req.has_active_charges,
        active_charge_count=req.active_charge_count,
        verification_status=req.verification_status,
    )

    return {
        "crn": norm_crn,
        "company_name": company_name,
        "strategies_count": len(strategies),
        "strategies": [s.model_dump() for s in strategies],
    }


@router.post("/campaigns/plan")
def plan_campaign_economics(
    payload: PlanEconomicsRequest,
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
) -> CampaignEconomicsModel:
    """
    Calculates full-funnel commercial unit economics across Conservative, Base,
    and Optimistic scenarios, computing break-even thresholds and risk-bounded test budgets.
    """
    model = CampaignEconomicsService.calculate_economics(
        facility_size_gbp=payload.facility_size_gbp,
        campaign_cost_gbp=payload.campaign_cost_gbp,
        target_reach=payload.target_reach,
        strategy_id=payload.strategy_id,
        is_historical_benchmark=payload.is_historical_benchmark,
        observed_rates=payload.observed_rates,
    )
    return model


@router.post("/campaigns/track")
def track_campaign(
    payload: TrackCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """
    Issues a deterministic campaign tracking identifier formatted as CMP-YYYYMM-XXXXXXXX
    along with standardized UTM parameters.
    """
    service = OutcomeLearningService(db)
    try:
        record = service.issue_campaign_tracking_id(
            strategy_id=payload.strategy_id,
            channel_name=payload.channel_name,
            channel_type=payload.channel_type,
            content_format=payload.content_format,
            crn=payload.crn,
            budget_gbp=payload.budget_gbp,
            owner_user_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "campaign_id": record.campaign_id,
        "strategy_id": record.strategy_id,
        "crn": record.crn,
        "channel_name": record.channel_name,
        "channel_type": record.channel_type,
        "utm_campaign": record.utm_campaign,
        "utm_source": record.utm_source,
        "utm_medium": record.utm_medium,
        "budget_gbp": record.budget_gbp,
        "status": record.status,
        "owner_user_id": record.owner_user_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.post("/campaigns/{campaign_id}/status")
def transition_campaign_status(
    campaign_id: str,
    payload: CampaignStatusTransitionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """Transitions a campaign tracking record between permitted lifecycle states."""
    service = OutcomeLearningService(db)
    try:
        record = service.transition_campaign_status(
            campaign_id=campaign_id,
            target_status=payload.target_status,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
        )
        return {
            "campaign_id": record.campaign_id,
            "status": record.status,
            "owner_user_id": record.owner_user_id,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/campaigns")
def list_tracked_campaigns(
    crn: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """Lists active and historical tracked campaigns visible to the authenticated user."""
    q = db.query(CampaignTrackingRecord)
    if (current_user.role or "").upper() not in ("ADMIN", "ANALYST"):
        q = q.filter(CampaignTrackingRecord.owner_user_id == current_user.id)
    if crn:
        norm_crn, valid, _ = normalize_uk_crn(crn)
        if valid and norm_crn:
            q = q.filter(CampaignTrackingRecord.crn == norm_crn)
    records = q.order_by(CampaignTrackingRecord.created_at.desc()).all()
    return {
        "count": len(records),
        "campaigns": [
            {
                "campaign_id": r.campaign_id,
                "strategy_id": r.strategy_id,
                "crn": r.crn,
                "channel_name": r.channel_name,
                "channel_type": r.channel_type,
                "content_format": r.content_format,
                "utm_campaign": r.utm_campaign,
                "budget_gbp": r.budget_gbp,
                "status": r.status,
                "owner_user_id": r.owner_user_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


@router.post("/campaigns/outcomes")
def record_campaign_outcome(
    payload: IngestOutcomeEventRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """Records an individual funnel outcome event for a campaign."""
    service = OutcomeLearningService(db)
    try:
        event = service.record_campaign_outcome_event(
            campaign_id=payload.campaign_id,
            event_type=payload.event_type,
            crn=payload.crn,
            funded_amount_gbp=payload.funded_amount_gbp,
            realized_commission_gbp=payload.realized_commission_gbp,
            event_timestamp=payload.event_timestamp,
            source_reference=payload.source_reference,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
            enforce_campaign_exists=True,
        )
        is_new = not getattr(event, "is_duplicate", False)
        return {
            "status": "RECORDED" if is_new else "DUPLICATE_IGNORED",
            "event_id": event.id,
            "campaign_id": event.campaign_id,
            "event_type": event.event_type,
            "is_new": is_new,
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/outcomes/csv")
def ingest_campaign_outcomes_csv(
    payload: IngestOutcomeCsvRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """Ingests a batch of campaign outcomes via CSV with strict ownership and deduplication checks."""
    service = OutcomeLearningService(db)
    try:
        counts = service.ingest_campaign_outcomes_csv(
            csv_content=payload.csv_content,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
        )
        return {
            "status": "COMPLETED",
            "counts": counts,
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/campaigns/metrics")
def get_campaign_metrics(
    campaign_id: Optional[str] = None,
    as_of: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ALL_AUTHENTICATED_ROLES)),
):
    """
    Returns empirical funnel conversion rates, volume, and commission metrics.
    Supports point-in-time snapshotting with as_of to prevent future leakage.
    """
    service = OutcomeLearningService(db)
    if campaign_id:
        try:
            service._verify_campaign_access(
                campaign_id=campaign_id,
                actor_user_id=current_user.id,
                actor_role=current_user.role,
                require_active=False,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    metrics = service.compute_empirical_campaign_conversion_rates(
        campaign_id=campaign_id,
        as_of=as_of,
    )
    return metrics
