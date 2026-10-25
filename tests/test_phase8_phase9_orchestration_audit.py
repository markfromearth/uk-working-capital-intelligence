"""
Phase 8 & Phase 9 Verification Suite:
Multi-Agent Orchestration Control Boundaries, Zero-Budget Enforcement,
and Cryptographic Tamper-Evident Audit Chain Verification.
"""

from datetime import timezone
import hashlib
import hmac
from typing import Any, Dict, List, Tuple
import pytest
from sqlalchemy.orm import Session

from backend.app.agents.base import BaseAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.agents.independent_verification import IndependentVerificationAgent
from backend.app.agents.orchestrator import MultiAgentOrchestrator
from backend.app.config import settings
from backend.app.database import Base, engine, SessionLocal
from backend.app.models.company import CompanyRegistry
from backend.app.models.marketing import CampaignTrackingRecord
from backend.app.models.user import AuditCheckpoint, AuditLog
from backend.app.services.audit import (
    GENESIS_HASH,
    compute_checkpoint_signature,
    compute_record_hash,
    log_audit_event,
    verify_audit_log_chain,
)


@pytest.fixture(scope="module", autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        db.query(AuditCheckpoint).delete()
        db.query(AuditLog).delete()
        db.commit()
        yield db
    finally:
        db.query(AuditCheckpoint).delete()
        db.query(AuditLog).delete()
        db.commit()
        db.close()


class _PaidDummyAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentRole.SIGNAL_DETECTION)

    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        return {"signals_detected_count": 1}, [{"source": "PAID_BUREAU", "uri": "https://example.com"}], 2.50


@pytest.mark.asyncio
async def test_zero_budget_limit_blocks_any_positive_cost():
    """
    Phase 8 Mandatory Negative Test:
    When budget_limit_gbp == 0.0 (free operating mode), any agent incurring cost > 0.0
    must immediately return AgentTaskStatus.BUDGET_EXCEEDED.
    Previously, BaseAgent had `if cost_gbp > request.budget_limit_gbp and request.budget_limit_gbp > 0.0:`.
    """
    agent = _PaidDummyAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.SIGNAL_DETECTION,
        crn="08931201",
        budget_limit_gbp=0.00,
    )
    res = await agent.execute(req)
    assert res.status == AgentTaskStatus.BUDGET_EXCEEDED
    assert "exceeded task limit" in (res.error_message or "")


@pytest.mark.asyncio
async def test_verification_agent_blocks_ambiguous_entity_and_simulated_live_claim():
    """
    Phase 8 Mandatory Negative Test:
    Agent J must reject traces where EntityResolutionAgent flagged an ambiguous entity match
    or where simulated signals are used when require_live_evidence=True.
    """
    verifier = IndependentVerificationAgent()
    prior = [
        {
            "role": AgentRole.SIGNAL_DETECTION.value,
            "status": "COMPLETED",
            "cost_incurred_gbp": 0.0,
            "output_payload": {
                "signals_detected_count": 1,
                "signals": [{"signal_type": "dbt_increase", "verification_status": "simulated"}],
            },
            "evidence_references": [{"source": "CREDITSAFE", "uri": "https://example.com"}],
        },
        {
            "role": AgentRole.ENTITY_RESOLUTION.value,
            "status": "COMPLETED",
            "cost_incurred_gbp": 0.0,
            "output_payload": {
                "canonical_crn": "08931201",
                "is_ambiguous": True,
                "review_id": 42,
            },
            "evidence_references": [],
        },
    ]
    req = AgentTaskRequest(
        target_role=AgentRole.INDEPENDENT_VERIFICATION,
        crn="08931201",
        budget_limit_gbp=0.0,
        input_payload={"prior_results": prior, "require_live_evidence": True},
    )
    res = await verifier.execute(req)
    assert res.output_payload["is_fully_verified"] is False
    assert res.output_payload["verification_status"] == "REJECTED"
    failures = res.output_payload["failures"]
    assert any("ambiguous entity match" in f for f in failures)
    assert any("Simulated signals detected" in f for f in failures)


@pytest.mark.asyncio
async def test_orchestrator_revokes_campaign_when_compliance_fails(db_session: Session):
    """
    Phase 8 Mandatory Negative Test:
    When target company in CompanyRegistry is an English general partnership (individual subscriber
    under PECR Reg 22 without opt-in), MultiAgentOrchestrator must fail verification and mark the
    issued campaign tracking record as SUPPRESSED.
    """
    crn = "07654321"
    db_session.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).delete()
    db_session.add(
        CompanyRegistry(
            crn=crn,
            company_name="Smith & Sons General Partnership",
            company_status="active",
            company_type="partnership",
            sic_codes="49410",
        )
    )
    db_session.commit()

    orchestrator = MultiAgentOrchestrator()
    trace = await orchestrator.run_investigation_pipeline(crn=crn, total_budget_gbp=0.0)
    assert trace.is_fully_verified is False
    assert trace.rejection_reason is not None
    assert "ComplianceRiskAgent blocked" in trace.rejection_reason

    # Verify campaign record in DB was automatically suppressed
    h_trace = next(t for t in trace.task_traces if t.role == AgentRole.CAMPAIGN_MEASUREMENT)
    cid = h_trace.output_payload.get("campaign_id")
    assert cid is not None
    rec = db_session.query(CampaignTrackingRecord).filter(CampaignTrackingRecord.campaign_id == cid).first()
    assert rec is not None
    assert rec.status == "SUPPRESSED"


def test_audit_chain_rejects_null_or_empty_prev_hash_and_record_hash(db_session: Session):
    """
    Phase 9 Mandatory Negative Test:
    Setting prev_hash=None/"" or record_hash=None/"" on an AuditLog row MUST fail
    verify_audit_log_chain (previously bypassed due to `if r.prev_hash and ...`).
    """
    e1 = log_audit_event(db_session, action="TEST_1", resource_type="CAMPAIGN", resource_id="C1")
    e2 = log_audit_event(db_session, action="TEST_2", resource_type="CAMPAIGN", resource_id="C2")

    valid, count, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert valid is True
    assert count == 2

    # 1. Bypass attempt: set prev_hash to None
    orig_prev = e2.prev_hash
    e2.prev_hash = None
    db_session.commit()
    valid, _, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert valid is False
    assert "prev_hash is missing or empty" in (err or "")
    e2.prev_hash = orig_prev
    db_session.commit()

    # 2. Bypass attempt: set record_hash to empty string
    orig_hash = e1.record_hash
    e1.record_hash = ""
    db_session.commit()
    valid, _, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert valid is False
    assert "record_hash is missing or empty" in (err or "")
    e1.record_hash = orig_hash
    db_session.commit()


def test_audit_chain_detects_recomputed_chain_without_checkpoint_key(db_session: Session):
    """
    Phase 9 Mandatory Negative Test:
    Even if an attacker with DB write access modifies an audit entry AND recomputes
    all subsequent record_hash values in AuditLog, signed AuditCheckpoint verification
    must detect the tampering.
    """
    e1 = log_audit_event(db_session, action="APPROVE_CAMPAIGN", resource_type="CAMPAIGN", resource_id="C100", user_id="u1")
    e2 = log_audit_event(db_session, action="EXPORT_LEADS", resource_type="CAMPAIGN", resource_id="C100", user_id="u1")

    # Attacker modifies e1.action and recomputes both e1.record_hash and e2.prev_hash + e2.record_hash
    e1.action = "BENIGN_READ"
    ts1 = int(e1.created_at.replace(tzinfo=timezone.utc).timestamp()) if e1.created_at.tzinfo is None else int(e1.created_at.timestamp())
    e1.record_hash = compute_record_hash(
        prev_hash=GENESIS_HASH,
        action=e1.action,
        resource_type=e1.resource_type,
        resource_id=e1.resource_id,
        details_json=e1.details_json,
        timestamp_epoch=ts1,
        user_id=e1.user_id,
    )
    e2.prev_hash = e1.record_hash
    ts2 = int(e2.created_at.replace(tzinfo=timezone.utc).timestamp()) if e2.created_at.tzinfo is None else int(e2.created_at.timestamp())
    e2.record_hash = compute_record_hash(
        prev_hash=e2.prev_hash,
        action=e2.action,
        resource_type=e2.resource_type,
        resource_id=e2.resource_id,
        details_json=e2.details_json,
        timestamp_epoch=ts2,
        user_id=e2.user_id,
    )
    db_session.commit()

    # Checkpoints catch the re-hashed chain!
    valid, _, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
    assert valid is False
    assert "hash mismatch against signed checkpoint" in (err or "")


def test_checkpoint_hmac_uses_domain_separated_key():
    """
    Verify compute_checkpoint_signature does NOT use raw settings.SECRET_KEY directly.
    """
    sig = compute_checkpoint_signature(sequence=1, last_id=10, last_hash="abc123", total_records=10)
    raw_jwt_key_sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"1:10:abc123:10",
        hashlib.sha256,
    ).hexdigest()
    assert sig != raw_jwt_key_sig
