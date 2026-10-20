import pytest
from datetime import datetime, timezone
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.services.ingestion import IngestionService
from backend.app.database import SessionLocal, init_db
from backend.app.models.signal import StreamCheckpoint, NormalizedSignal

@pytest.fixture(scope="module")
def setup_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()

def test_companies_house_charge_parsing_and_mr04_scope(setup_db):
    adapter = CompaniesHouseAdapter()
    
    # 1. Full satisfaction (MR04)
    full_chg = {
        "id": "chg-test-001",
        "status": "fully-satisfied",
        "satisfied_on": "2026-09-15",
        "classification": {"description": "Debenture Fixed and Floating Charge"},
        "persons_entitled": [{"name": "Barclays Bank PLC"}]
    }
    signals = adapter._parse_charge_into_signals("08931201", full_chg)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["signal_type"] == "charge_satisfaction_mr04"
    assert sig["value_numeric"] == 1.0  # Full scope
    assert sig["confidence_score"] == 0.95
    assert "Barclays Bank PLC" in sig["metadata_json"]

    # 2. Partial satisfaction
    partial_chg = {
        "id": "chg-test-002",
        "status": "partially-satisfied",
        "satisfied_on": "2026-09-20",
        "classification": {"description": "Legal Mortgage"},
        "persons_entitled": [{"name": "HSBC UK Bank PLC"}]
    }
    signals_p = adapter._parse_charge_into_signals("08931201", partial_chg)
    assert len(signals_p) == 1
    assert signals_p[0]["value_numeric"] == 0.5  # Partial scope

def test_stream_checkpoint_persistence_and_recovery(setup_db):
    db = setup_db
    ingestion = IngestionService(db)
    stream_name = f"test_charges_stream_{datetime.now(timezone.utc).timestamp()}"

    # Initially no checkpoint
    cp0 = ingestion.get_checkpoint(stream_name)
    assert cp0 is None

    # Advance checkpoint
    ingestion.update_checkpoint(stream_name, "1050", processed_count=5)
    cp1 = ingestion.get_checkpoint(stream_name)
    assert cp1 == "1050"

    # Simulate restart & advance again
    ingestion.update_checkpoint(stream_name, "1075", processed_count=10)
    cp2 = ingestion.get_checkpoint(stream_name)
    assert cp2 == "1075"

    checkpoint_record = db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
    assert checkpoint_record.events_processed_count == 15
