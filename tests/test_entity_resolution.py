import pytest
from datetime import datetime, timezone
from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.services.resolution import EntityResolutionService

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    # Add a sample target company
    comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == "08931201").first()
    if not comp:
        comp = CompanyRegistry(
            crn="08931201",
            company_name="Apex Precision Engineering Ltd",
            registered_office_address="Unit 4, Gateway Industrial Estate, Birmingham, B6 7EU",
            company_status="active",
            created_at=datetime.now(timezone.utc)
        )
        db.add(comp)
        db.commit()
    yield db
    db.close()

def test_exact_crn_resolution(db_session):
    resolver = EntityResolutionService(db_session)
    # With valid formatted or unformatted CRN
    comp, score, method, review_id = resolver.resolve_entity(raw_identifier="8931201")
    assert comp is not None
    assert comp.crn == "08931201"
    assert score == 1.0
    assert method == "exact_crn"
    assert review_id is None

def test_high_confidence_deterministic_match(db_session):
    resolver = EntityResolutionService(db_session)
    # Name variation with identical postcode
    comp, score, method, review_id = resolver.resolve_entity(
        company_name="Apex Precision Engineering Limited",
        postcode="B6 7EU"
    )
    assert comp is not None
    assert comp.crn == "08931201"
    assert score >= 0.90
    assert review_id is None

def test_ambiguous_match_routes_to_review_queue(db_session):
    resolver = EntityResolutionService(db_session)
    # Ambiguous match (spelling variation without verified CRN)
    comp, score, method, review_id = resolver.resolve_entity(
        company_name="Apex Engineering Services",
        postcode="B6 99X"
    )
    # Must NOT automatically merge ambiguous record
    assert comp is None
    if 0.65 <= score < 0.90:
        assert method == "routed_to_review_queue"
        assert review_id is not None
        
        # Verify review queue item
        rev = db_session.query(CompanyMatchReview).filter(CompanyMatchReview.id == review_id).first()
        assert rev.status == "pending"
        
        # Approve manually
        updated_rev = resolver.apply_manual_review_decision(review_id, approved=True, reviewer_name="Analyst A", notes="Verified trading address")
        assert updated_rev.status == "approved"
