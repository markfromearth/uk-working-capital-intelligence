import pytest
from sqlalchemy import inspect
from backend.app.database import engine, init_db, SessionLocal
from backend.app.models import CompanyRegistry, NormalizedSignal, LiquidityScoreRecord, User
from backend.app.core.security import hash_password, verify_password

def test_database_initialization():
    init_db()
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    assert "company_registry" in tables
    assert "normalized_signals" in tables
    assert "raw_events" in tables
    assert "stream_checkpoints" in tables
    assert "company_feature_snapshots" in tables
    assert "liquidity_score_records" in tables
    assert "score_contribution_records" in tables
    assert "broker_review_actions" in tables
    assert "users" in tables
    assert "audit_logs" in tables

def test_password_hashing():
    pw = "SecureBrokerSecret2026!"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
