"""
Tests for PECR Corporate Subscriber Rules, SSRF Protection, and Cryptographic Audit Verification.
Ensures zero unauthorized marketing activations, blocks adversarial network attacks,
and validates tamper-evident audit integrity.
"""

import pytest
from sqlalchemy.orm import Session

from backend.app.agents.compliance_risk import ComplianceRiskAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest, AgentTaskStatus
from backend.app.core.ssrf_protection import validate_safe_url
from backend.app.database import Base, engine, SessionLocal
from backend.app.models.decision_maker import SuppressionRecord
from backend.app.models.user import AuditLog, AuditCheckpoint
from backend.app.services.audit import log_audit_event, verify_audit_log_chain, create_signed_audit_checkpoint
from backend.app.services.identity_resolution import IdentityResolutionService


@pytest.fixture(scope="module", autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_pecr_corporate_subscriber_validation():
    """Verify PECR Reg 22 exemption for corporate bodies, Reg 23 sender/opt-out rules, and Reg 22 opt-in consent for sole traders."""
    # 1. Corporate Subscribers (Ltd, PLC, LLP, Scottish Partnership) -> CLEARED under Reg 22 Corporate Subscriber Distinction & Reg 23
    for ctype in ["ltd", "private-limited-company", "plc", "llp", "scottish-partnership"]:
        ok, status_code, legal_basis, _ = IdentityResolutionService.validate_corporate_subscriber_status(
            company_type=ctype,
            crn="08931201",
        )
        assert ok is True
        assert status_code == "CORPORATE_SUBSCRIBER_EXEMPT"
        assert "PECR 2003 Regulation 22" in legal_basis
        assert "PECR Regulation 23" in legal_basis

    # 2. Missing or unknown company_type -> FAIL-CLOSED (never defaults to 'ltd')
    ok_missing, status_missing, _, _ = IdentityResolutionService.validate_corporate_subscriber_status(
        company_type=None,
        crn="08931201",
    )
    assert ok_missing is False
    assert status_missing == "UNKNOWN_SUBSCRIBER_STATUS_BLOCKED"

    # 3. Individual / Sole Trader / English Partnership without prior consent -> BLOCKED under Reg 22
    ok_sole, status_code_sole, legal_basis_sole, rationale_sole = (
        IdentityResolutionService.validate_corporate_subscriber_status(
            company_type="sole-trader",
            crn="08931201",
            has_prior_opt_in_consent=False,
        )
    )
    assert ok_sole is False
    assert status_code_sole == "NON_CORPORATE_REQUIRES_OPT_IN"
    assert "PECR 2003 Regulation 22" in legal_basis_sole
    assert "prohibited under PECR Regulation 22 without recorded prior opt-in consent" in rationale_sole

    # 4. Individual / Sole Trader with verifiable prior consent -> CLEARED
    ok_consent, status_code_consent, _, _ = (
        IdentityResolutionService.validate_corporate_subscriber_status(
            company_type="sole-trader",
            crn="08931201",
            has_prior_opt_in_consent=True,
        )
    )
    assert ok_consent is True
    assert status_code_consent == "OPT_IN_CONSENT_VERIFIED"


def test_ssrf_protection_filters():
    """Verify robust defense against SSRF, internal hostnames, metadata endpoints, and non-HTTP schemes."""
    # Prohibited non-HTTP schemes
    assert validate_safe_url("file:///etc/passwd")[0] is False
    assert validate_safe_url("ftp://ftp.internal.local/passwords.txt")[0] is False
    assert validate_safe_url("gopher://127.0.0.1:70")[0] is False

    # Prohibited loopback & private addresses
    assert validate_safe_url("http://127.0.0.1:8000/api")[0] is False
    assert validate_safe_url("http://localhost:3000/metrics")[0] is False
    assert validate_safe_url("http://192.168.1.100/admin")[0] is False
    assert validate_safe_url("http://10.0.0.1/status")[0] is False
    assert validate_safe_url("http://172.16.0.5/api")[0] is False

    # Prohibited Cloud Instance Metadata endpoints (AWS / GCP / Azure)
    assert validate_safe_url("http://169.254.169.254/latest/meta-data/")[0] is False
    assert validate_safe_url("http://metadata.google.internal/computeMetadata/v1/")[0] is False
    assert validate_safe_url("http://instance-data/latest/meta-data/")[0] is False

    # Legitimate public HTTPS endpoints (allowed without network DNS call in test mode)
    assert validate_safe_url("https://www.rha.uk.net", resolve_dns=False)[0] is True
    assert validate_safe_url("https://builduk.org/about", resolve_dns=False)[0] is True


def test_cryptographic_audit_chain_tamper_detection(db_session: Session):
    """Verify that verify_audit_log_chain detects any altered record or broken SHA-256 link."""
    db_session.query(AuditCheckpoint).delete()
    db_session.query(AuditLog).delete()
    db_session.commit()

    # Write legitimate audit record
    rec1 = log_audit_event(
        db=db_session,
        action="compliance_check",
        resource_type="company",
        resource_id="08931201",
        details={"status": "CLEARED"},
    )
    rec2 = log_audit_event(
        db=db_session,
        action="strategy_generated",
        resource_type="strategy",
        resource_id="STRAT-001",
        details={"model": "v1"},
    )

    try:
        valid, count, err = verify_audit_log_chain(db_session, verify_checkpoints=True)
        assert valid is True
        assert count >= 2
        assert err is None

        # Adversarial test: Tamper with rec1 action
        original_action = rec1.action
        rec1.action = "TAMPERED_ACTION"
        db_session.commit()

        tamper_valid, _, tamper_err = verify_audit_log_chain(db_session, verify_checkpoints=True)
        assert tamper_valid is False
        assert tamper_err is not None
        assert "Tamper detected" in tamper_err
    finally:
        db_session.query(AuditCheckpoint).filter(AuditCheckpoint.last_audit_id.in_([rec1.id, rec2.id])).delete(synchronize_session=False)
        db_session.delete(rec2)
        db_session.delete(rec1)
        db_session.commit()
        create_signed_audit_checkpoint(db_session)


@pytest.mark.asyncio
async def test_compliance_risk_agent_adversarial_rejections(db_session: Session):
    """Verify Agent I rejects non-corporate subscribers and malicious URLs."""
    agent = ComplianceRiskAgent(db=db_session)
    assert agent.role == AgentRole.COMPLIANCE_RISK

    # Scenario 1: Sole trader without consent -> MUST BE REJECTED
    req_sole = AgentTaskRequest(
        target_role=AgentRole.COMPLIANCE_RISK,
        crn="08931201",
        input_payload={
            "company_type": "sole-trader",
            "has_prior_opt_in_consent": False,
        },
    )
    res_sole = await agent.execute(req_sole)
    assert res_sole.status == AgentTaskStatus.COMPLETED
    assert res_sole.output_payload["is_compliance_cleared"] is False
    assert any("PECR_VIOLATION" in v for v in res_sole.output_payload["violations"])

    # Scenario 2: Attack with SSRF URL -> MUST BE REJECTED
    req_ssrf = AgentTaskRequest(
        target_role=AgentRole.COMPLIANCE_RISK,
        crn="08931201",
        input_payload={
            "company_type": "ltd",
            "urls_to_verify": ["http://169.254.169.254/latest/meta-data/iam/security-credentials/"],
        },
    )
    res_ssrf = await agent.execute(req_ssrf)
    assert res_ssrf.status == AgentTaskStatus.COMPLETED
    assert res_ssrf.output_payload["is_compliance_cleared"] is False
    assert any("SSRF_VIOLATION" in v for v in res_ssrf.output_payload["violations"])

    # Scenario 3: Legitimate Corporate Subscriber (Ltd) with safe external URL -> CLEARED
    req_valid = AgentTaskRequest(
        target_role=AgentRole.COMPLIANCE_RISK,
        crn="08931201",
        input_payload={
            "company_type": "ltd",
            "urls_to_verify": ["https://www.rha.uk.net"],
            "dns_resolution_map": {"www.rha.uk.net": ["104.26.10.220"]},
        },
    )
    res_valid = await agent.execute(req_valid)
    assert res_valid.status == AgentTaskStatus.COMPLETED
    assert res_valid.output_payload["is_compliance_cleared"] is True
    assert res_valid.output_payload["violations_count"] == 0
    assert len(res_valid.evidence_references) >= 1
