import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.models.user import AuditLog
from backend.app.core.logging import logger

def compute_record_hash(prev_hash: str, action: str, resource_type: str, resource_id: str, details_json: str, timestamp_epoch: int) -> str:
    content = f"{prev_hash}|{action}|{resource_type}|{resource_id}|{details_json}|{timestamp_epoch}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def log_audit_event(
    db: Session,
    action: str,
    resource_type: str,
    resource_id: str,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None
) -> AuditLog:
    """Creates a tamper-evident audit log record linked to the previous record's hash."""
    now = datetime.now(timezone.utc)
    epoch_sec = int(now.timestamp())
    details_str = json.dumps(details or {}, sort_keys=True, default=str)

    # Get latest audit entry to link chain
    last_entry = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev_hash = last_entry.record_hash if (last_entry and last_entry.record_hash) else "GENESIS_BLOCK_HASH_UK_WORKING_CAPITAL_2026"

    record_hash = compute_record_hash(
        prev_hash=prev_hash,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details_str,
        timestamp_epoch=epoch_sec
    )

    audit_entry = AuditLog(
        user_id=user_id,
        user_email=user_email,
        user_role=user_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details_str,
        ip_address=ip_address,
        prev_hash=prev_hash,
        record_hash=record_hash,
        created_at=now
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry

def verify_audit_log_chain(db: Session) -> Tuple[bool, int, Optional[str]]:
    """
    Verifies the cryptographic hash chain of the audit log.
    Returns: (is_valid, records_checked, error_detail)
    """
    records = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    if not records:
        return True, 0, None

    expected_prev = "GENESIS_BLOCK_HASH_UK_WORKING_CAPITAL_2026"
    for r in records:
        # Check chain link
        if r.prev_hash and r.prev_hash != expected_prev:
            return False, r.id, f"Audit chain break at record {r.id}: prev_hash mismatch"

        # Calculate epoch
        ts = r.created_at
        if ts is None:
            epoch_sec = 0
        elif ts.tzinfo is None:
            epoch_sec = int(ts.replace(tzinfo=timezone.utc).timestamp())
        else:
            epoch_sec = int(ts.timestamp())

        # Verify integrity of record contents
        calc_hash = compute_record_hash(
            prev_hash=r.prev_hash or expected_prev,
            action=r.action,
            resource_type=r.resource_type,
            resource_id=r.resource_id,
            details_json=r.details_json or "{}",
            timestamp_epoch=epoch_sec
        )
        if r.record_hash and r.record_hash != calc_hash:
            return False, r.id, f"Tamper detected at audit record {r.id}: record_hash recalculation failed"

        if r.record_hash:
            expected_prev = r.record_hash

    return True, len(records), None
