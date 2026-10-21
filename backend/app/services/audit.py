import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.user import AuditLog, AuditCheckpoint
from backend.app.core.logging import logger

GENESIS_HASH = "GENESIS_BLOCK_HASH_UK_WORKING_CAPITAL_2026"

def compute_record_hash(
    prev_hash: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details_json: str,
    timestamp_epoch: int,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    ip_address: Optional[str] = None
) -> str:
    """
    Computes canonical SHA-256 hash covering ALL audit record fields
    including user identity and provenance IP address.
    """
    canonical_parts = [
        prev_hash,
        str(user_id or ""),
        str(user_email or ""),
        str(user_role or ""),
        str(action or ""),
        str(resource_type or ""),
        str(resource_id or ""),
        str(details_json or "{}"),
        str(ip_address or ""),
        str(timestamp_epoch)
    ]
    content = "|".join(canonical_parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def compute_checkpoint_signature(sequence: int, last_id: int, last_hash: str, total_records: int) -> str:
    """Computes HMAC-SHA256 signature for independently protected audit checkpoints."""
    payload = f"{sequence}:{last_id}:{last_hash}:{total_records}".encode("utf-8")
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()

def create_signed_audit_checkpoint(db: Session) -> Optional[AuditCheckpoint]:
    """Creates a new signed checkpoint covering the audit log up to the latest record."""
    last_record = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    if not last_record:
        return None

    total_records = db.query(AuditLog).count()
    last_cp = db.query(AuditCheckpoint).order_by(AuditCheckpoint.checkpoint_sequence.desc()).first()
    next_seq = (last_cp.checkpoint_sequence + 1) if last_cp else 1

    sig = compute_checkpoint_signature(
        sequence=next_seq,
        last_id=last_record.id,
        last_hash=last_record.record_hash or "",
        total_records=total_records
    )

    checkpoint = AuditCheckpoint(
        checkpoint_sequence=next_seq,
        last_audit_id=last_record.id,
        last_record_hash=last_record.record_hash or "",
        total_records=total_records,
        signature=sig,
        created_at=datetime.now(timezone.utc)
    )
    db.add(checkpoint)
    db.commit()
    db.refresh(checkpoint)
    return checkpoint

def log_audit_event(
    db: Session,
    action: str,
    resource_type: str,
    resource_id: str,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    create_checkpoint: bool = True
) -> AuditLog:
    """Creates a tamper-evident audit log record linked to the previous record's hash."""
    now = datetime.now(timezone.utc)
    epoch_sec = int(now.timestamp())
    details_str = json.dumps(details or {}, sort_keys=True, default=str)

    # Get latest audit entry to link chain
    last_entry = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev_hash = last_entry.record_hash if (last_entry and last_entry.record_hash) else GENESIS_HASH

    record_hash = compute_record_hash(
        prev_hash=prev_hash,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details_str,
        timestamp_epoch=epoch_sec,
        user_id=user_id,
        user_email=user_email,
        user_role=user_role,
        ip_address=ip_address
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

    if create_checkpoint:
        try:
            create_signed_audit_checkpoint(db)
        except Exception as e:
            logger.warning(f"Audit checkpoint creation non-fatal error: {e}")

    return audit_entry

def verify_audit_log_chain(db: Session, verify_checkpoints: bool = True) -> Tuple[bool, int, Optional[str]]:
    """
    Verifies:
      1. Cryptographic hash chain of all audit records sequentially.
      2. Record-level integrity across all fields (action, resource, details, user, ip).
      3. Link continuity (prev_hash == record_hash of predecessor).
      4. If verify_checkpoints=True: Verifies HMAC signature of the latest checkpoint
         and detects any truncation or unauthorized re-hashing of latest records.

    Returns: (is_valid, records_checked, error_detail)
    """
    records = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    if not records:
        # Check if checkpoints exist despite 0 records (tampered truncation of entire log)
        if verify_checkpoints:
            cp = db.query(AuditCheckpoint).first()
            if cp and cp.total_records > 0:
                return False, 0, f"Tamper detected: Entire audit log deleted! Checkpoint expected {cp.total_records} records."
        return True, 0, None

    expected_prev = GENESIS_HASH
    for r in records:
        # Check chain link
        if r.prev_hash and r.prev_hash != expected_prev:
            return False, r.id, f"Audit chain break at record {r.id}: prev_hash mismatch (expected {expected_prev[:12]}, got {r.prev_hash[:12]})"

        # Calculate epoch
        ts = r.created_at
        if ts is None:
            epoch_sec = 0
        elif ts.tzinfo is None:
            epoch_sec = int(ts.replace(tzinfo=timezone.utc).timestamp())
        else:
            epoch_sec = int(ts.timestamp())

        # Verify integrity of record contents including user and provenance fields
        calc_hash = compute_record_hash(
            prev_hash=r.prev_hash or expected_prev,
            action=r.action,
            resource_type=r.resource_type,
            resource_id=r.resource_id,
            details_json=r.details_json or "{}",
            timestamp_epoch=epoch_sec,
            user_id=r.user_id,
            user_email=r.user_email,
            user_role=r.user_role,
            ip_address=r.ip_address
        )
        if r.record_hash and r.record_hash != calc_hash:
            return False, r.id, f"Tamper detected at audit record {r.id}: record_hash recalculation failed"

        if r.record_hash:
            expected_prev = r.record_hash

    # Checkpoint verification: Detect truncation of tail records or unauthorized re-hashing
    if verify_checkpoints:
        latest_cp = db.query(AuditCheckpoint).order_by(AuditCheckpoint.checkpoint_sequence.desc()).first()
        if latest_cp:
            # 1. Verify HMAC signature of checkpoint
            expected_sig = compute_checkpoint_signature(
                sequence=latest_cp.checkpoint_sequence,
                last_id=latest_cp.last_audit_id,
                last_hash=latest_cp.last_record_hash,
                total_records=latest_cp.total_records
            )
            if not hmac.compare_digest(latest_cp.signature, expected_sig):
                return False, len(records), "Checkpoint HMAC signature invalid: unauthorized checkpoint modification"

            # 2. Check for log truncation
            if len(records) < latest_cp.total_records:
                return False, len(records), f"Tamper detected: Audit log was truncated! Found {len(records)} records, checkpoint requires {latest_cp.total_records}."

            # 3. Check terminal record matching checkpoint
            last_rec = records[-1]
            if last_rec.id < latest_cp.last_audit_id or last_rec.record_hash != latest_cp.last_record_hash:
                return False, len(records), f"Tamper detected: Terminal audit record hash mismatch against signed checkpoint."

    return True, len(records), None
