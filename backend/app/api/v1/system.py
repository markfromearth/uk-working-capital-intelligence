from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.models.signal import RawEvent, NormalizedSignal, DeadLetterEvent
from backend.app.models.score import LiquidityScoreRecord
from backend.app.providers.registry import provider_registry
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.audit import verify_audit_log_chain

router = APIRouter(prefix="/system", tags=["System & Health"])

@router.get("/audit-verification")
def audit_verification(db: Session = Depends(get_db)):
    """Verifies cryptographic hash chain of the immutable audit trail."""
    is_valid, count, err = verify_audit_log_chain(db)
    return {
        "chain_valid": is_valid,
        "records_verified": count,
        "error": err,
        "tamper_detected": not is_valid
    }

@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Comprehensive readiness and health check endpoint."""
    db_ok = True
    try:
        db.execute(func.now()).scalar()
    except Exception:
        db_ok = False

    return {
        "status": "healthy" if db_ok else "unhealthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_connected": db_ok,
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0"
    }

@router.get("/providers")
def get_providers_status():
    """
    Returns explicit capability and credential health for all 4 provider adapters.
    Distinguishes live from simulated vs unconfigured integrations.
    """
    return [p.model_dump() for p in provider_registry.get_health_summary()]

@router.post("/seed-demo-data")
def trigger_seed_demo_data(db: Session = Depends(get_db)):
    """Populates database with realistic UK companies, filing events, and signals."""
    seeded = seed_demonstration_data(db)
    return {"message": f"Successfully seeded {len(seeded)} demonstration companies and signal fixtures"}
