import json
import pytest
from datetime import datetime, timedelta, timezone
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.user import User
from backend.app.models.company import CompanyRegistry
from backend.app.models.score import LiquidityScoreRecord
from backend.app.models.investigation import CommercialOpportunityAssessment
from backend.app.models.decision_maker import (
    PersonIdentity,
    CompanyOfficer,
    ProfessionalRoleProfile,
    AudienceActivation,
    SuppressionRecord
)
from backend.app.core.security import create_access_token, hash_password
from backend.app.providers.registry import provider_registry
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.services.decision_maker_discovery import DecisionMakerDiscoveryService
from backend.app.services.identity_resolution import IdentityResolutionService
from backend.app.services.decision_maker_validation import DecisionMakerValidationGate
from backend.app.services.audience_activation import AudienceActivationService
from backend.app.services.investigation_engine import ValueOfInformationEngine

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_decision_maker_environment():
    init_db()
    db = SessionLocal()
    seed_demonstration_data(db)

    # Ensure admin and broker users exist
    admin_user = db.query(User).filter(User.email == "admin_dm@test.com").first()
    if not admin_user:
        admin_user = User(
            email="admin_dm@test.com",
            hashed_password=hash_password("AdminSecure2026!"),
            full_name="Compliance Officer",
            role="admin",
            is_active=1
        )
        db.add(admin_user)

    broker_user = db.query(User).filter(User.email == "broker_dm@test.com").first()
    if not broker_user:
        broker_user = User(
            email="broker_dm@test.com",
            hashed_password=hash_password("BrokerSecure2026!"),
            full_name="Outreach Broker",
            role="broker",
            is_active=1
        )
        db.add(broker_user)

    db.commit()
    db.close()

def get_auth_headers(role="broker", user_id=None):
    if not user_id:
        db = SessionLocal()
        u = db.query(User).filter(User.role == role, User.is_active == 1).first()
        user_id = u.id if u else "unknown_user"
        db.close()
    token = create_access_token(subject=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}

# 1. Statutory Discovery & Relationship Disambiguation

def test_companies_house_officer_and_psc_discovery(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    adapter = provider_registry.get_adapter("companies_house")
    monkeypatch.setattr(adapter, "_is_live", False)
    db = SessionLocal()
    crn = "08931201" # Apex Precision Engineering Ltd

    discovery = DecisionMakerDiscoveryService(db)
    res = discovery.discover_company_personnel(crn)
    assert res["officers_found"] >= 3
    assert res["psc_found"] >= 1

    # Verify CompanyOfficer records created
    officers = db.query(CompanyOfficer).filter(CompanyOfficer.crn == crn).all()
    roles = [o.officer_role for o in officers]
    assert "director" in roles
    assert "secretary" in roles
    assert "psc" in roles

    # Verify distinction of categories
    categories = {o.relationship_category for o in officers}
    assert "directorship" in categories
    assert "secretarial" in categories
    assert "ownership" in categories or "control" in categories

    # Verify historical ceased appointment handling
    resigned_dir = db.query(CompanyOfficer).filter(
        CompanyOfficer.crn == crn,
        CompanyOfficer.is_active == False
    ).first()
    assert resigned_dir is not None
    assert resigned_dir.resigned_on is not None

    # Verify PSC ownership percentage extraction
    psc_rec = db.query(CompanyOfficer).filter(
        CompanyOfficer.crn == crn,
        CompanyOfficer.officer_role == "psc"
    ).first()
    assert psc_rec is not None
    assert psc_rec.ownership_percentage_lower is not None
    assert psc_rec.ownership_percentage_lower >= 25.0

    db.close()

# 2. Strict Rule: PSC Is Not Assumed to Be Finance Decision-Maker

def test_psc_is_not_assumed_to_be_finance_decision_maker(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    adapter = provider_registry.get_adapter("companies_house")
    monkeypatch.setattr(adapter, "_is_live", False)
    db = SessionLocal()
    crn = "08931201"

    discovery = DecisionMakerDiscoveryService(db)
    discovery.discover_company_personnel(crn)

    # Check Finance Director role vs PSC role
    fd_profile = db.query(ProfessionalRoleProfile).filter(
        ProfessionalRoleProfile.crn == crn,
        ProfessionalRoleProfile.role_title == "Finance Director"
    ).first()
    assert fd_profile is not None
    assert fd_profile.is_finance_decision_maker is True

    # An individual who is purely a PSC has an ownership profile, but is NOT marked as finance DM
    psc_profile = db.query(ProfessionalRoleProfile).filter(
        ProfessionalRoleProfile.crn == crn,
        ProfessionalRoleProfile.source_provider == "companies_house_psc"
    ).first()
    if psc_profile:
        assert psc_profile.is_finance_decision_maker is False

    db.close()

# 3. Multi-Signal Identity Resolution: Names Alone Cannot Validate

def test_identity_resolution_rejects_name_alone():
    db = SessionLocal()
    crn = "08931201"

    # Create uncorroborated candidate profile with name match only
    person = PersonIdentity(
        full_name="John Anonymous Doe",
        normalized_name="john anonymous doe"
    )
    db.add(person)
    db.flush()

    profile = ProfessionalRoleProfile(
        person_id=person.person_id,
        crn=crn,
        role_title="Head of Procurement",
        is_finance_decision_maker=False,
        platform="third_party_unverified",
        source_provider="unverified_web_mention",
        match_confidence=0.40,
        verification_status="unverified"
    )
    db.add(profile)
    db.commit()

    resolver = IdentityResolutionService(db)
    resolved = resolver.resolve_profile_verification(profile.id)

    # Strict Acceptance Criterion: Name alone is insufficient for validated match
    assert resolved.verification_status != "validated"
    assert resolved.verification_status == "candidate"
    assert resolved.match_confidence < 0.75

    db.close()

# 4. Contradictory Evidence & Staleness Detection

def test_contradictory_evidence_and_staleness_routing():
    db = SessionLocal()
    crn = "08931201"

    # Create person linked to resigned director
    person = PersonIdentity(full_name="Resigned Director", normalized_name="resigned director")
    db.add(person)
    db.flush()

    resigned_officer = CompanyOfficer(
        crn=crn,
        person_id=person.person_id,
        officer_name="Resigned Director",
        officer_role="director",
        appointed_on=datetime(2015, 1, 1, tzinfo=timezone.utc),
        resigned_on=datetime(2023, 1, 1, tzinfo=timezone.utc),
        is_active=False
    )
    db.add(resigned_officer)

    # Candidate profile falsely claiming current employment
    profile = ProfessionalRoleProfile(
        person_id=person.person_id,
        crn=crn,
        role_title="Managing Director",
        is_current=True,
        platform="companies_house",
        source_provider="companies_house",
        last_verified_at=datetime.now(timezone.utc) - timedelta(days=200) # Stale (>180d)
    )
    db.add(profile)
    db.commit()

    resolver = IdentityResolutionService(db)
    resolved = resolver.resolve_profile_verification(profile.id)

    # Must route to disputed due to resigned appointment contradiction
    assert resolved.verification_status == "disputed"
    assert "resigned/ceased" in resolved.contradictory_evidence

    db.close()

# 5. Validation Gate Independence Rule: High Company Strain Cannot Override Unverified Person

def test_validation_gate_independence_rule():
    db = SessionLocal()
    crn = "08931201"

    # Ensure high company strain score exists
    score_rec = db.query(LiquidityScoreRecord).filter(LiquidityScoreRecord.crn == crn).first()
    if not score_rec:
        score_rec = LiquidityScoreRecord(
            crn=crn,
            score=92.5,
            score_band="strong",
            evidence_coverage="strong",
            model_version="v1.0.0",
            scored_at=datetime.now(timezone.utc)
        )
        db.add(score_rec)
        db.commit()

    # Create unverified candidate person
    person = PersonIdentity(full_name="Unverified Candidate", normalized_name="unverified candidate")
    db.add(person)
    db.flush()

    profile = ProfessionalRoleProfile(
        person_id=person.person_id,
        crn=crn,
        role_title="Finance Director",
        is_finance_decision_maker=True,
        match_confidence=0.40,
        verification_status="candidate"  # Not validated!
    )
    db.add(profile)
    db.commit()

    gate = DecisionMakerValidationGate(db)
    res = gate.evaluate_decision_maker_gates(crn, profile.id)

    # Gate 1 (Company Opportunity) passes because strain is high
    g1 = next(d for d in res.dimensions if d.dimension == "company_opportunity_confidence")
    assert g1.passed is True

    # Gate 2 (Person Identity Confidence) FAILS because profile is candidate (not validated)
    g2 = next(d for d in res.dimensions if d.dimension == "person_identity_confidence")
    assert g2.passed is False

    # MANDATORY RULE: Overall gate FAILS closed! High company score cannot compensate for uncertain identity!
    assert res.overall_passed is False
    assert res.can_activate_audience is False
    assert any("Identity is 'candidate'" in reason for reason in res.failing_reasons)

    db.close()

# 6. Suppression Registry & GDPR Article 17 Right to Erasure

def test_suppression_and_right_to_erasure():
    db = SessionLocal()
    crn = "08931201"
    resolver = IdentityResolutionService(db)

    # 1. Test Suppression / Objection (GDPR Art 21)
    person = PersonIdentity(full_name="Opt Out Individual", normalized_name="opt out individual")
    db.add(person)
    db.commit()

    supp = resolver.record_suppression_objection(
        identifier_type="person_id",
        identifier_value=person.person_id,
        suppression_reason="gdpr_article_21_objection"
    )
    assert supp.is_active is True
    assert person.is_suppressed is True

    # 2. Test Right to Erasure (GDPR Art 17)
    person_to_erase = PersonIdentity(full_name="Erase Me", normalized_name="erase me", birth_year=1980)
    db.add(person_to_erase)
    db.commit()

    erasure_res = resolver.execute_right_to_erasure(person_to_erase.person_id)
    assert erasure_res["success"] is True

    # Verify personal identifying data is permanently redacted
    refreshed_person = db.query(PersonIdentity).filter(PersonIdentity.person_id == person_to_erase.person_id).first()
    assert "[REDACTED_ERASURE_" in refreshed_person.full_name
    assert refreshed_person.birth_year is None
    assert refreshed_person.is_erased is True
    assert refreshed_person.is_suppressed is True

    db.close()

# 7. Audience Activation Compliance Protection: Rejection of Distress Targeting & Fail-Closed Gates

def test_audience_activation_compliance_guards():
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    headers = get_auth_headers(role="admin", user_id=admin.id)
    crn = "08931201"

    # 1. Prohibited financial distress assertion MUST be rejected immediately (HTTP 400)
    distress_payload = {
        "campaign_name": "Target Insolvent and Struggling Companies",
        "crn": crn,
        "destination_platform": "linkedin_company_targeting",
        "activation_type": "company_level_account",
        "purpose_specification": "Targeting desperate business owners in severe financial distress",
        "lawful_basis": "legitimate_interests"
    }
    resp = client.post("/api/v1/audiences/", json=distress_payload, headers=headers)
    assert resp.status_code == 400
    assert "prohibited financial distress" in resp.json()["detail"]

    # 2. Valid company-level B2B activation proposal
    valid_payload = {
        "campaign_name": "Q4 Working-Capital Awareness Campaign",
        "crn": crn,
        "destination_platform": "linkedin_company_targeting",
        "activation_type": "company_level_account",
        "purpose_specification": "B2B commercial finance facility awareness for expanding mid-market companies",
        "lawful_basis": "legitimate_interests",
        "legitimate_interest_assessment_ref": "LIA-2026-B2B-WC-01"
    }
    create_resp = client.post("/api/v1/audiences/", json=valid_payload, headers=headers)
    assert create_resp.status_code == 201
    act_data = create_resp.json()
    act_id = act_data["activation_id"]
    assert act_data["status"] == "pending_compliance_review"

    # 3. Fail-Closed: Execution fails if not approved by compliance review
    exec_resp = client.post(f"/api/v1/audiences/{act_id}/execute", headers=headers)
    assert exec_resp.status_code == 400
    assert "not compliance-approved" in exec_resp.json()["detail"]

    # 4. Compliance Officer approves
    review_resp = client.post(f"/api/v1/audiences/{act_id}/review", json={
        "approve": True,
        "review_notes": "Reviewed against LIA-2026-B2B-WC-01; privacy notice transparent; suppression cleared."
    }, headers=headers)
    assert review_resp.status_code == 200
    assert review_resp.json()["compliance_status"] == "approved"

    # 5. Execution succeeds once approved
    exec_resp2 = client.post(f"/api/v1/audiences/{act_id}/execute", headers=headers)
    assert exec_resp2.status_code == 200
    assert exec_resp2.json()["status"] == "active"

    db.close()

# 8. Adaptive Investigation EVOI Economics for Person Discovery

def test_evoi_person_discovery_economics():
    db = SessionLocal()
    engine = ValueOfInformationEngine(db)

    # For high-strain company (Apex Precision 08931201), officers lookup tool is approved and evaluated
    ch_officers_dec = engine.evaluate_single_tool("08931201", "companies_house_officers_lookup", remaining_budget=10.0)
    assert ch_officers_dec.tool_name == "companies_house_officers_lookup"
    assert ch_officers_dec.estimated_cost_gbp == 0.00
    assert ch_officers_dec.decision in ("INVESTIGATE", "STOP")  # STOP if already executed in last 24h, else INVESTIGATE

    # For an unqualified / low strain dummy company (e.g. 09999999), person discovery MUST yield STOP
    unqualified_crn = "09999999"
    unq_company = db.query(CompanyRegistry).filter(CompanyRegistry.crn == unqualified_crn).first()
    if not unq_company:
        unq_company = CompanyRegistry(crn=unqualified_crn, company_name="Dormant Inactive Co Ltd", company_status="active")
        db.add(unq_company)
        db.commit()

    unq_dec = engine.evaluate_single_tool(unqualified_crn, "companies_house_officers_lookup", remaining_budget=10.0)
    # Must STOP because company opportunity is unverified
    assert unq_dec.decision == "STOP"
    assert "unverified (low strain score)" in unq_dec.decision_rationale

    db.close()

# 9. Campaign Performance Measurement: Separating Impressions from Intent

def test_campaign_performance_measurement():
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    headers = get_auth_headers(role="admin", user_id=admin.id)
    crn = "08931201"

    # Create and approve an audience activation
    act_service = AudienceActivationService(db)
    act = act_service.create_activation_request(
        campaign_name="Measurement Test Campaign",
        crn=crn,
        destination_platform="platform_abm_native",
        activation_type="company_level_account",
        purpose_specification="B2B liquidity intelligence",
        lawful_basis="legitimate_interests",
        legitimate_interest_assessment_ref="LIA-01"
    )
    act_service.review_and_approve_activation(act.activation_id, admin.id, True, "Approved")
    act_service.execute_activation(act.activation_id, admin.id)

    # Record metrics via API
    metrics_payload = {
        "spend_gbp": 125.00,
        "impressions_count": 4500,
        "clicks_count": 42,
        "engagements_count": 18,
        "inquiries_received": 2,
        "qualified_conversations": 1,
        "facilities_funded_count": 1,
        "funded_volume_gbp": 300000.00
    }
    resp = client.post(f"/api/v1/audiences/{act.activation_id}/metrics", json=metrics_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["facilities_funded"] == 1

    # Verify activation detail endpoint includes metrics
    detail_resp = client.get(f"/api/v1/audiences/{act.activation_id}")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert len(detail_data["metrics"]) >= 1
    assert detail_data["metrics"][0]["impressions"] == 4500
    assert detail_data["metrics"][0]["funded_volume_gbp"] == 300000.00

    db.close()
