"""
FastAPI REST API router for Companies House Invoice Finance Warden.
Exposes endpoints for screening companies, listing qualified leads, and inspecting trapped cash dossiers.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.schemas.warden import LendingArchetype, TrappedCashDossier, ScreeningResult
from backend.app.services.pipeline_orchestrator import default_pipeline_orchestrator

router = APIRouter(prefix="/warden", tags=["Invoice Finance Warden (CH-Only)"])


class ScreenCompanyRequest(BaseModel):
    weights: Optional[Dict[str, float]] = None
    toggles: Optional[Dict[str, bool]] = None
    archetype: LendingArchetype = LendingArchetype.PRIME_INVOICE_DISCOUNTING
    min_gate_threshold: float = Field(default=65.0, ge=0.0, le=100.0)


@router.post("/screen/{crn}")
async def screen_company(
    crn: str,
    payload: Optional[ScreenCompanyRequest] = None,
):
    """
    Execute autonomous two-stage screening on a UK Company:
    1. Stage 1: Structured screening & provisional scoring.
    2. Stage 2 (if Score >= Threshold): Quant accounts extraction & borrowing base sizing.
    """
    req = payload or ScreenCompanyRequest()
    result = await default_pipeline_orchestrator.process_company(
        crn=crn,
        weights=req.weights,
        toggles=req.toggles,
        archetype=req.archetype,
        min_gate_threshold=req.min_gate_threshold,
    )

    if result.get("status") == "INVALID_CRN":
        raise HTTPException(status_code=400, detail=result.get("message"))
    if result.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=result.get("message"))

    return result


@router.get("/prospects", response_model=List[TrappedCashDossier])
def list_prospects():
    """List all qualified invoice finance prospects sorted by indicative facility capacity."""
    return default_pipeline_orchestrator.get_prospects()


@router.get("/prospects/{crn}/dossier", response_model=TrappedCashDossier)
def get_prospect_dossier(crn: str):
    """Retrieve detailed financial extraction dossier and outreach pitch for a qualified lead."""
    dossier = default_pipeline_orchestrator.get_dossier(crn)
    if not dossier:
        raise HTTPException(
            status_code=404,
            detail=f"No qualified dossier found for CRN {crn}. Run /warden/screen/{crn} first.",
        )
    return dossier


@router.get("/health")
def get_warden_health():
    """Health and status of the CH-Only Warden pipeline."""
    return {
        "status": "HEALTHY",
        "mode": "CH_ONLY_MVP",
        "live_api_configured": default_pipeline_orchestrator.ch_client._is_live,
        "token_bucket_available": round(default_pipeline_orchestrator.ch_client.rate_limiter.tokens, 2),
        "total_qualified_prospects": len(default_pipeline_orchestrator._dossier_store),
    }
