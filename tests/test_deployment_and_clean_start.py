from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.core.security import create_access_token, hash_password
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.experian import ExperianAdapter
from backend.app.providers.workforce import WorkforceAdapter
from backend.app.providers.base import AdapterHealthStatus

client = TestClient(app)

def test_missing_credentials_does_not_silently_promote_fixtures_when_demo_mode_false():
    """
    Proves that when commercial credentials are absent and DEMO_MODE=False,
    the adapters truthfully return UNCONFIGURED status and empty data,
    PREVENTING silent substitution of simulated data for live data.
    """
    with patch.object(settings, "DEMO_MODE", False):
        # 1. Companies House unconfigured
        ch_adapter = CompaniesHouseAdapter(api_key=None)
        ch_adapter._is_live = False
        assert ch_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert ch_adapter.fetch_company_profile("08931201") is None
        assert ch_adapter.fetch_company_charges("08931201") == []

        # 2. Creditsafe unconfigured
        cs_adapter = CreditsafeAdapter(api_key=None)
        cs_adapter._is_live = False
        assert cs_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert cs_adapter.fetch_company_signals("08931201") == []

        # 3. Experian unconfigured
        exp_adapter = ExperianAdapter(api_key=None)
        exp_adapter._is_live = False
        assert exp_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert exp_adapter.fetch_company_signals("08931201") == []

        # 4. Workforce unconfigured
        wf_adapter = WorkforceAdapter(api_key=None)
        wf_adapter._is_live = False
        assert wf_adapter.get_health_status().status == AdapterHealthStatus.UNCONFIGURED
        assert wf_adapter.fetch_company_signals("08931201") == []

def test_clean_health_and_system_providers_endpoint():
    """Verifies that the /api/v1/system/health and /api/v1/system/providers endpoints respond accurately."""
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "deploy_test_broker@test.co.uk").first()
        if not user:
            user = User(
                email="deploy_test_broker@test.co.uk",
                hashed_password=hash_password("DeployBrokerPass2026!"),
                full_name="Deploy Test Broker",
                role="broker",
                is_active=1,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(subject=user.id, role=user.role)
    finally:
        db.close()

    health_resp = client.get("/api/v1/system/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] == "healthy"
    assert health_data["database_connected"] is True
    assert health_data["version"] == "1.0.0"

    prov_resp = client.get("/api/v1/system/providers", headers={"Authorization": f"Bearer {token}"})
    assert prov_resp.status_code == 200
    prov_data = prov_resp.json()
    assert len(prov_data) >= 4
    names = [p["provider_name"] for p in prov_data]
    assert "companies_house" in names
    assert "contracts_finder" in names
    assert "the_gazette" in names
    assert "creditsafe" in names
    assert "experian" in names
    assert "workforce_intelligence" in names


def test_operational_recovery_backup_restore_migration_and_audit_verification(tmp_path):
    """
    Pilot-Entry Gate Section 5 Mandatory Verification:
    Tests end-to-end operational recovery:
    1. Database initialization & idempotent schema migration on a fresh SQLite file.
    2. Creation of canonical company, active suppression record, approved activation, and checkpointed audit log chain.
    3. Online SQLite backup via `sqlite3.Connection.backup()`.
    4. Simulated post-backup corruption/truncation of AuditLog detected via `verify_audit_log_chain(verify_checkpoints=True)`.
    5. Database restoration from backup, restart `init_db()` idempotency check, audit chain verification pass,
       and confirmation that restored suppression records remain enforced at execution time.
    """
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.database import Base
    from backend.app.models.company import CompanyRegistry
    from backend.app.models.decision_maker import SuppressionRecord
    from backend.app.models.user import AuditLog
    from backend.app.services.audience_activation import AudienceActivationService
    from backend.app.services.audit import create_signed_audit_checkpoint, log_audit_event, verify_audit_log_chain
    from backend.app.services.identity_resolution import IdentityResolutionService

    live_db_path = tmp_path / "pilot_live.db"
    backup_db_path = tmp_path / "pilot_backup.db"

    engine = create_engine(f"sqlite:///{live_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    # Step 1: Populate state + checkpointed audit chain
    db = Session()
    try:
        comp = CompanyRegistry(
            crn="08931201",
            company_name="Recovery Verification Ltd",
            company_status="active",
            company_type="ltd",
        )
        db.add(comp)
        db.commit()

        act_svc = AudienceActivationService(db)
        act = act_svc.create_activation_request(
            campaign_name="Pilot Recovery Test Campaign",
            crn="08931201",
            destination_platform="linkedin_company_targeting",
            activation_type="company_level_account",
            purpose_specification="B2B working capital awareness",
            lawful_basis="legitimate_interests",
            legitimate_interest_assessment_ref="LIA-REC-01",
            requester_user_id="broker-rec-1",
        )
        act_svc.review_and_approve_activation(
            activation_id=act.activation_id,
            reviewer_user_id="admin-rec-1",
            approve=True,
            review_notes="Approved before suppression",
        )

        # Record suppression objection + audit events + checkpoint
        id_svc = IdentityResolutionService(db)
        id_svc.record_suppression_objection(
            identifier_type="crn",
            identifier_value="08931201",
            suppression_reason="gdpr_article_21_objection",
        )
        log_audit_event(db, action="pilot_recovery_test", resource_type="company", resource_id="08931201")
        cp = create_signed_audit_checkpoint(db)
        assert cp is not None

        ok_pre, count_pre, err_pre = verify_audit_log_chain(db, verify_checkpoints=True)
        assert ok_pre is True
        assert count_pre >= 2
        assert err_pre is None
        saved_act_id = act.activation_id
    finally:
        db.close()
    engine.dispose()

    # Step 2: Online SQLite Backup
    src_conn = sqlite3.connect(str(live_db_path))
    bak_conn = sqlite3.connect(str(backup_db_path))
    with bak_conn:
        src_conn.backup(bak_conn)
    src_conn.close()
    bak_conn.close()

    # Step 3: Simulate post-backup tampering / tail truncation on live DB -> Detected by verify_audit_log_chain
    engine_corrupt = create_engine(f"sqlite:///{live_db_path}", connect_args={"check_same_thread": False})
    SessionCorrupt = sessionmaker(bind=engine_corrupt)
    db_corrupt = SessionCorrupt()
    try:
        last_log = db_corrupt.query(AuditLog).order_by(AuditLog.id.desc()).first()
        db_corrupt.delete(last_log)
        db_corrupt.commit()

        ok_corrupt, _, err_corrupt = verify_audit_log_chain(db_corrupt, verify_checkpoints=True)
        assert ok_corrupt is False
        assert "truncated" in err_corrupt.lower() or "tamper" in err_corrupt.lower()
    finally:
        db_corrupt.close()
    engine_corrupt.dispose()

    # Step 4: Restore from backup snapshot and verify integrity + suppression enforcement
    bak_conn = sqlite3.connect(str(backup_db_path))
    restored_conn = sqlite3.connect(str(live_db_path))
    with restored_conn:
        bak_conn.backup(restored_conn)
    bak_conn.close()
    restored_conn.close()

    engine_restored = create_engine(f"sqlite:///{live_db_path}", connect_args={"check_same_thread": False})
    # Re-run metadata create_all to verify idempotent restart schema check
    Base.metadata.create_all(bind=engine_restored)
    SessionRestored = sessionmaker(bind=engine_restored)
    db_restored = SessionRestored()
    try:
        ok_restored, count_restored, err_restored = verify_audit_log_chain(db_restored, verify_checkpoints=True)
        assert ok_restored is True
        assert count_restored == count_pre
        assert err_restored is None

        # Verify suppression record survived restore and still blocks execution of the previously approved activation
        supp_restored = (
            db_restored.query(SuppressionRecord)
            .filter(SuppressionRecord.identifier_value == "08931201", SuppressionRecord.is_active == True)
            .first()
        )
        assert supp_restored is not None

        act_svc_restored = AudienceActivationService(db_restored)
        with pytest.raises(ValueError, match="suppression registry prior to execution"):
            act_svc_restored.execute_activation(saved_act_id, actor_user_id="broker-rec-1")
    finally:
        db_restored.close()
    engine_restored.dispose()
