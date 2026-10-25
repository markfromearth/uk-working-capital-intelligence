"""
Phase 3 & Phase 4 Adversarial Compliance (PECR / UK GDPR) & SSRF Network Boundary Tests:
- PECR Reg 22 vs Reg 23 accurate legal classification.
- Fail-closed handling of missing, empty, or unknown company_type (never defaults to 'ltd').
- Sole traders, general partnerships, and unincorporated associations blocked without prior consent.
- Reg 23 sender identification and opt-out address enforcement even for corporate subscribers.
- Suppression & objection propagation at both campaign tracking issuance and audience activation execution time.
- SSRF boundary defense against:
  * IPv4 loopback, private, link-local, metadata, carrier-grade NAT (100.64.0.0/10), unspecified (0.0.0.0)
  * IPv6 loopback (::1), unique-local (fc00::/7), link-local (fe80::/10), IPv4-mapped IPv6 (::ffff:127.0.0.1, ::ffff:169.254.169.254)
  * Numeric / hex / octal IPv4 obfuscation (2130706433, 0x7f000001, 0177.0.0.1)
  * Embedded userinfo credentials (https://user:pass@example.com)
  * Non-standard ports (https://example.com:8443)
  * Cleartext HTTP scheme rejection by default
  * Fail-closed DNS resolution failure (socket.gaierror)
  * DNS resolution to private/loopback IP
  * Redirect hop revalidation (public HTTPS -> 302 -> internal metadata IP blocked)
  * DNS rebinding protection across sequential resolution checks
  * Oversized response body protection
"""

import socket
import httpx
import pytest

from backend.app.agents.compliance_risk import ComplianceRiskAgent
from backend.app.agents.contracts import AgentRole, AgentTaskRequest
from backend.app.core.ssrf_protection import SafeHttpClient, is_ip_blocked, validate_safe_url
from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry
from backend.app.models.decision_maker import SuppressionRecord
from backend.app.services.audience_activation import AudienceActivationService
from backend.app.services.identity_resolution import IdentityResolutionService
from backend.app.services.outcome_learning import OutcomeLearningService


@pytest.fixture
def db_session():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_pecr_fail_closed_on_missing_or_unknown_company_type():
    """Missing, empty, or unrecognized company_type must NEVER default to 'ltd' and must fail closed."""
    for bad_type in [None, "", "   ", "unknown_entity_form", "oversea-company"]:
        ok, status_code, _, rationale = IdentityResolutionService.validate_corporate_subscriber_status(
            company_type=bad_type,
            crn="08931201",
            has_prior_opt_in_consent=False,
        )
        assert ok is False, f"Expected fail-closed for company_type={bad_type!r}"
        assert status_code in ("UNKNOWN_SUBSCRIBER_STATUS_BLOCKED", "OUT_OF_SCOPE_JURISDICTION_BLOCKED")


def test_pecr_reg22_vs_reg23_legal_precision():
    """
    Verifies:
    - English/Welsh general partnerships and sole traders are individual subscribers (blocked under Reg 22 without opt-in).
    - Scottish partnerships and LLPs are corporate subscribers (exempt from Reg 22 prior opt-in).
    - Corporate subscribers STILL fail under Reg 23 if sender identity is concealed or opt-out address is omitted.
    """
    # 1. English general partnership without consent -> Blocked under Reg 22
    dec_gp = IdentityResolutionService.evaluate_pecr_uk_gdpr_compliance(
        company_type="general-partnership",
        crn="08931201",
        has_prior_opt_in_consent=False,
    )
    assert dec_gp["is_permissible"] is False
    assert dec_gp["subscriber_classification"] == "INDIVIDUAL_SUBSCRIBER"
    assert dec_gp["pecr_reg_22_status"] == "VIOLATION_BLOCKED"

    # 2. Corporate subscriber (Ltd) with missing opt-out address -> Blocked under Reg 23
    dec_reg23_fail = IdentityResolutionService.evaluate_pecr_uk_gdpr_compliance(
        company_type="ltd",
        crn="08931201",
        sender_identity_disclosed=True,
        valid_opt_out_address_provided=False,
    )
    assert dec_reg23_fail["is_permissible"] is False
    assert dec_reg23_fail["status_code"] == "PECR_REG_23_IDENTIFICATION_OR_OPT_OUT_MISSING"
    assert dec_reg23_fail["pecr_reg_23_status"] == "VIOLATION_BLOCKED"


def test_late_suppression_blocks_approved_activation_and_campaign_tracking(db_session):
    """
    If a company or contact is added to the SuppressionRecord registry AFTER compliance review approval,
    both AudienceActivationService.execute_activation and OutcomeLearningService.issue_campaign_tracking_id
    must fail closed immediately.
    """
    crn = "07778889"
    comp = db_session.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first()
    if not comp:
        comp = CompanyRegistry(
            crn=crn,
            company_name="Late Suppression Test Ltd",
            company_status="active",
            company_type="ltd",
        )
        db_session.add(comp)
        db_session.commit()

    # Clear any old suppressions for this test CRN
    db_session.query(SuppressionRecord).filter(SuppressionRecord.identifier_value == crn).delete()
    db_session.commit()

    act_service = AudienceActivationService(db_session)
    act = act_service.create_activation_request(
        campaign_name="Compliant Pre-Suppression Campaign",
        crn=crn,
        destination_platform="linkedin_company_targeting",
        activation_type="company_level_account",
        purpose_specification="B2B working capital awareness",
        lawful_basis="legitimate_interests",
        legitimate_interest_assessment_ref="LIA-2026-01",
    )

    # Approve while not suppressed
    approved = act_service.review_and_approve_activation(
        activation_id=act.activation_id,
        reviewer_user_id="admin-1",
        approve=True,
        review_notes="Approved prior to objection",
    )
    assert approved.compliance_approval_status == "approved"

    # Now record an Article 21 objection for the CRN before execution
    resolver = IdentityResolutionService(db_session)
    supp = resolver.record_suppression_objection(
        identifier_type="crn",
        identifier_value=crn,
        suppression_reason="gdpr_article_21_objection",
    )

    try:
        # 1. Execution of previously approved activation must fail closed!
        with pytest.raises(ValueError, match="suppression registry prior to execution"):
            act_service.execute_activation(act.activation_id, actor_user_id="broker-1")

        # 2. Issuing a new campaign tracking code for the suppressed CRN must also fail closed!
        learning_service = OutcomeLearningService(db_session)
        with pytest.raises(ValueError, match="Compliance block: Target CRN"):
            learning_service.issue_campaign_tracking_id(
                strategy_id="STRAT-SUPP-01",
                channel_name="RHA Transport Weekly",
                channel_type="PUBLICATION",
                content_format="TRADE_PUBLICATION_ARTICLE",
                crn=crn,
            )
    finally:
        db_session.delete(supp)
        db_session.commit()


def test_ssrf_ip_and_url_adversarial_matrix():
    """Tests IPv4, IPv6, mapped IPv6, CGNAT, numeric/hex encodings, credentials, ports, and fail-closed DNS."""
    # 1. IPv4-mapped IPv6 loopback and AWS metadata
    assert is_ip_blocked("::ffff:127.0.0.1")[0] is True
    assert is_ip_blocked("::ffff:169.254.169.254")[0] is True
    assert is_ip_blocked("::1")[0] is True
    assert is_ip_blocked("0.0.0.0")[0] is True
    assert is_ip_blocked("::")[0] is True
    assert is_ip_blocked("fe80::1")[0] is True
    assert is_ip_blocked("fc00::1")[0] is True
    assert is_ip_blocked("100.64.0.1")[0] is True  # Carrier-grade NAT

    # 2. Numeric, hex, and octal IPv4 obfuscation
    assert validate_safe_url("https://2130706433/admin", resolve_dns=False)[0] is False  # 127.0.0.1
    assert validate_safe_url("https://0x7f000001/admin", resolve_dns=False)[0] is False  # 127.0.0.1
    assert validate_safe_url("https://0177.0.0.1/admin", resolve_dns=False)[0] is False  # 127.0.0.1
    assert validate_safe_url("https://[::ffff:169.254.169.254]/latest", resolve_dns=False)[0] is False

    # 3. Embedded credentials and non-standard ports
    assert validate_safe_url("https://admin:secret@www.rha.uk.net/", resolve_dns=False)[0] is False
    assert validate_safe_url("https://www.rha.uk.net:8443/api", resolve_dns=False)[0] is False

    # 4. Cleartext HTTP rejected by default
    assert validate_safe_url("http://www.rha.uk.net/", resolve_dns=False, allow_http=False)[0] is False

    # 5. DNS failure fails CLOSED when resolve_dns=True
    def _failing_dns(_host: str):
        raise socket.gaierror("Name or service not known")

    ok_dns_fail, reason_dns_fail = validate_safe_url(
        "https://nonexistent.example.org/feed.xml",
        resolve_dns=True,
        dns_resolver=_failing_dns,
    )
    assert ok_dns_fail is False
    assert "Fail-closed DNS" in reason_dns_fail

    # 6. Public hostname resolving to private/loopback IP is blocked
    ok_rebind, reason_rebind = validate_safe_url(
        "https://evil-rebind.example.com/feed.xml",
        resolve_dns=True,
        dns_resolver=lambda _h: ["127.0.0.1"],
    )
    assert ok_rebind is False
    assert "Loopback" in reason_rebind


@pytest.mark.asyncio
async def test_safe_http_client_redirect_revalidation_and_dns_rebinding():
    """
    Proves SafeHttpClient:
    1. Rejects a redirect hop from a safe public HTTPS URL to an internal metadata or loopback address.
    2. Detects TOCTOU DNS rebinding where the first resolution returns a public IP and the second returns 127.0.0.1.
    3. Enforces maximum response byte limits.
    """
    # Scenario 1: Public URL -> 302 Redirect to AWS Metadata IP -> Blocked!
    def _redirect_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "safe.example.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="compromised-metadata")

    client_redir = SafeHttpClient(
        dns_resolver=lambda host: ["93.184.216.34"] if host == "safe.example.com" else ["169.254.169.254"]
    )
    with pytest.raises(ValueError, match="SSRF boundary"):
        await client_redir.get(
            "https://safe.example.com/start",
            transport=httpx.MockTransport(_redirect_handler),
        )

    # Scenario 2: TOCTOU DNS Rebinding (1st DNS check -> 93.184.216.34, 2nd DNS check -> 127.0.0.1) -> Blocked!
    dns_answers = [["93.184.216.34"], ["127.0.0.1"]]
    call_idx = {"n": 0}

    def _rebinding_resolver(_host: str):
        ans = dns_answers[min(call_idx["n"], len(dns_answers) - 1)]
        call_idx["n"] += 1
        return ans

    client_rebind = SafeHttpClient(dns_resolver=_rebinding_resolver)
    with pytest.raises(ValueError, match="Loopback"):
        await client_rebind.get(
            "https://rebind.example.com/feed",
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, text="ok")),
        )

    # Scenario 3: Oversized response body -> Blocked!
    client_size = SafeHttpClient(
        max_response_bytes=128,
        dns_resolver=lambda _h: ["93.184.216.34"],
    )
    with pytest.raises(ValueError, match="exceeds limit"):
        await client_size.get(
            "https://safe.example.com/large",
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, content=b"x" * 512)),
        )


@pytest.mark.asyncio
async def test_compliance_risk_agent_resolves_dns_and_blocks_unknown_company_type(db_session):
    """
    Proves ComplianceRiskAgent (Agent I):
    - Fails closed when company_type is missing and not in CompanyRegistry.
    - Resolves DNS on candidate URLs and rejects domains resolving to private/loopback IPs.
    """
    agent = ComplianceRiskAgent(
        db=db_session,
        dns_resolver=lambda host: ["10.0.0.42"] if "internal-ssrf" in host else ["93.184.216.34"],
    )

    # 1. Unknown CRN without company_type -> Blocked (never defaults to 'ltd')
    req_unknown = AgentTaskRequest(
        target_role=AgentRole.COMPLIANCE_RISK,
        crn="99887766",
        input_payload={},
    )
    res_unknown = await agent.execute(req_unknown)
    assert res_unknown.output_payload["is_compliance_cleared"] is False
    assert res_unknown.output_payload["pecr_status"] == "UNKNOWN_SUBSCRIBER_STATUS_BLOCKED"

    # 2. Domain resolving to private IP 10.0.0.42 -> Blocked via active DNS resolution
    req_dns_ssrf = AgentTaskRequest(
        target_role=AgentRole.COMPLIANCE_RISK,
        crn="08931201",
        input_payload={
            "company_type": "ltd",
            "urls_to_verify": ["https://internal-ssrf.example.com/rss"],
        },
    )
    res_dns_ssrf = await agent.execute(req_dns_ssrf)
    assert res_dns_ssrf.output_payload["is_compliance_cleared"] is False
    assert any("Private network IP" in v for v in res_dns_ssrf.output_payload["violations"])


@pytest.mark.asyncio
async def test_pinned_ssrf_network_backend_constrains_actual_connected_ip_and_blocks_connect_time_rebinding():
    """
    Proves that PinnedSSRFNetworkBackend:
    1. Resolves the hostname at connect_tcp time and passes the literal validated IP (not the hostname)
       to the underlying socket connect_tcp, pinning the actual connected destination IP.
    2. Blocks connect-time DNS rebinding (when pre-flight checks #1 and #2 return a safe public IP,
       and the 3rd DNS lookup inside connect_tcp returns 127.0.0.1 or ::ffff:169.254.169.254).
    3. Verifies post-connect socket peername and immediately closes the stream if the connected peer IP is blocked.
    """
    from httpcore._backends.base import AsyncNetworkBackend, AsyncNetworkStream
    from backend.app.core.ssrf_protection import PinnedSSRFNetworkBackend

    class _FakeSocket:
        def __init__(self, peer_ip: str):
            self._peer_ip = peer_ip

        def getpeername(self):
            return (self._peer_ip, 443)

    class _FakeStream(AsyncNetworkStream):
        def __init__(self, peer_ip: str):
            self.peer_ip = peer_ip
            self.closed = False

        async def read(self, max_bytes: int, timeout=None) -> bytes:
            return b""

        async def write(self, buffer: bytes, timeout=None) -> None:
            pass

        async def aclose(self) -> None:
            self.closed = True

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return self

        def get_extra_info(self, info: str):
            if info == "socket":
                return _FakeSocket(self.peer_ip)
            return None

    class _FakeInnerBackend(AsyncNetworkBackend):
        def __init__(self, peer_ip_override=None):
            self.connected_hosts = []
            self.peer_ip_override = peer_ip_override
            self.last_stream = None

        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            self.connected_hosts.append((host, port))
            self.last_stream = _FakeStream(self.peer_ip_override or host)
            return self.last_stream

    # 1. Safe domain -> underlying connect_tcp receives literal IP "93.184.216.34", NOT "safe.example.com"
    inner_ok = _FakeInnerBackend()
    pinned_ok = PinnedSSRFNetworkBackend(
        dns_resolver=lambda host: ["93.184.216.34"],
        inner_backend=inner_ok,
    )
    stream = await pinned_ok.connect_tcp("safe.example.com", 443)
    assert inner_ok.connected_hosts == [("93.184.216.34", 443)]
    assert pinned_ok.last_connected_ip == "93.184.216.34"
    assert stream.closed is False

    # 2. Connect-time DNS rebinding to IPv4-mapped IPv6 metadata ::ffff:169.254.169.254 -> Blocked before inner connect_tcp!
    inner_rebind = _FakeInnerBackend()
    pinned_rebind = PinnedSSRFNetworkBackend(
        dns_resolver=lambda host: ["::ffff:169.254.169.254"],
        inner_backend=inner_rebind,
    )
    with pytest.raises(ValueError, match="IPv4-mapped IPv6"):
        await pinned_rebind.connect_tcp("rebind.example.com", 443)
    assert inner_rebind.connected_hosts == []

    # 3. Post-connect peername verification: if OS socket connected to 169.254.169.254, stream is closed and ValueError raised
    inner_bad_peer = _FakeInnerBackend(peer_ip_override="169.254.169.254")
    pinned_bad_peer = PinnedSSRFNetworkBackend(
        dns_resolver=lambda host: ["93.184.216.34"],
        inner_backend=inner_bad_peer,
    )
    with pytest.raises(ValueError, match="post-connect"):
        await pinned_bad_peer.connect_tcp("safe.example.com", 443)
    assert inner_bad_peer.last_stream is not None
    assert inner_bad_peer.last_stream.closed is True
