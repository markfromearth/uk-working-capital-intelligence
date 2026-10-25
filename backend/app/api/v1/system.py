from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.user import User
from backend.app.api.v1.auth import require_role
from backend.app.providers.registry import provider_registry
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.audit import verify_audit_log_chain

router = APIRouter(prefix="/system", tags=["System & Health"])

@router.get("/audit-verification")
def audit_verification(
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst"])),
    db: Session = Depends(get_db)
):
    """
    Verifies cryptographic hash chain and signed checkpoints of the audit trail.
    Restricted to Admin and Analyst roles.
    """
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
def get_providers_status(
    current_user: User = Depends(require_role(["ADMIN", "admin", "ANALYST", "analyst", "BROKER", "broker"]))
):
    """
    Returns explicit capability and credential health for all provider adapters.
    Distinguishes live from simulated vs unconfigured integrations.
    """
    return [p.model_dump() for p in provider_registry.get_health_summary()]

@router.post("/seed-demo-data")
def trigger_seed_demo_data(
    current_user: User = Depends(require_role(["ADMIN", "admin"])),
    db: Session = Depends(get_db)
):
    """
    Populates database with realistic UK companies, filing events, and signals.
    Admin only; strictly forbidden in production.
    """
    if settings.ENVIRONMENT == "production":
        raise HTTPException(status_code=403, detail="Demonstration data seeding is forbidden in production environments.")
    seeded = seed_demonstration_data(db)
    return {"message": f"Successfully seeded {len(seeded)} demonstration companies and signal fixtures"}
