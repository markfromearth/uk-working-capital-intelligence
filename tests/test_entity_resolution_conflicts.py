import pytest
from datetime import datetime, timezone
from backend.app.database import SessionLocal, init_db
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.services.resolution import EntityResolutionService

@pytest.fixture(scope="module")
def db_session():
    init_db()
    db = SessionLocal()
    # Add target registered company
    comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == "08931201").first()
    if not comp:
        comp = CompanyRegistry(
            crn="08931201",
            company_name="Apex Precision Engineering Ltd",
            registered_office_address="Unit 4, Gateway Industrial Estate, Birmingham, B6 7EU",
            company_status="active"
        )
        db.add(comp)
        db.commit()
    yield db
    db.close()

def test_conflicting_crn_prevents_false_positive_merge(db_session):
    """
    CRITICAL: Verifies that an input carrying a valid CRN that differs from candidate CRN
    is strictly rejected from matching or merging, even with 100% name/address similarity.
    """
    resolver = EntityResolutionService(db_session)

    # Input has a DIFFERENT valid CRN ("09991234"), but identical name and postcode
    comp, score, method, review_id = resolver.resolve_entity(
        raw_identifier="09991234",  # Conflicting valid CRN
        company_name="Apex Precision Engineering Ltd",
        postcode="B6 7EU"
    )

    # Must NOT merge with "08931201"
    assert comp is None
    assert method != "deterministic_high_confidence"
    assert method != "exact_crn"

def test_entity_unmerge_and_split_reversal(db_session):
    resolver = EntityResolutionService(db_session)
    now = datetime.now(timezone.utc)

    # Create a match review that was previously approved
    review_entry = CompanyMatchReview(
        source_identifier="VANGUARD SERVICES UK",
        candidate_crn="08931201",
        candidate_name="Apex Precision Engineering Ltd",
        source_provider="credit_feed",
        match_score=0.75,
        resolution_method="probabilistic",
        status="approved",
        created_at=now
    )
    db_session.add(review_entry)
    db_session.commit()
    db_session.refresh(review_entry)

    # Now unmerge / reverse the decision
    unmerged = resolver.unmerge_or_split_entity(
        review_id=review_entry.id,
        reviewer_name="Senior Analyst Mark",
        reason="Corporate group members are legally independent operating entities"
    )

    assert unmerged is not None
    assert unmerged.status == "rejected"
    assert "UNMERGE_REVERSAL" in unmerged.reviewer_notes
    assert unmerged.reviewed_by == "Senior Analyst Mark"
