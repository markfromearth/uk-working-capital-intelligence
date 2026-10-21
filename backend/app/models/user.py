import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Integer, Index
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(50), default="broker", nullable=False)  # admin, analyst, broker
    is_active = Column(Integer, default=1)
    api_key = Column(String(64), unique=True, index=True, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime, nullable=True)

class AuditLog(Base):
    """Tamper-evident audit trail with cryptographic hash chaining for compliance."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=True, index=True)
    user_email = Column(String(255), nullable=True)
    user_role = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False, index=True)  # e.g. 'review_confirm', 'signal_dispute', 'login'
    resource_type = Column(String(50), nullable=False)        # 'company', 'signal', 'prospect', 'user'
    resource_id = Column(String(255), nullable=False)
    details_json = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    prev_hash = Column(String(64), nullable=True)            # SHA-256 of preceding audit record
    record_hash = Column(String(64), nullable=True)          # SHA-256 of current record contents + prev_hash
    created_at = Column(DateTime, default=utcnow, index=True)
