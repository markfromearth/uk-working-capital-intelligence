import pytest
from unittest.mock import patch, MagicMock
import httpx
from backend.app.providers.companies_house import CompaniesHouseAdapter

def test_companies_house_stream_416_recovery():
    """
    Tests that when Companies House stream returns HTTP 416 (timepoint cursor out of range / expired),
    the adapter catches the condition and resets next_timepoint to None (stream head) rather than crashing.
    """
    adapter = CompaniesHouseAdapter(api_key="mock_key")
    adapter._is_live = True  # test the live pathway mock

    mock_response = MagicMock()
    mock_response.status_code = 416

    with patch("httpx.stream") as mock_stream:
        # Context manager returns mock_response
        mock_stream.return_value.__enter__.return_value = mock_response

        result = adapter.poll_stream_events(timepoint="999999999", limit=10)
        assert result["has_more"] is False
        assert result["next_timepoint"] is None  # Reset cursor to head
        assert result.get("error") == "cursor_expired_reset"

def test_companies_house_charge_parsing_variations():
    adapter = CompaniesHouseAdapter()
    crn = "08931201"

    # Full scope debenture
    full_chg = {
        "id": "chg-var-001",
        "status": "fully-satisfied",
        "satisfied_on": "2026-08-01",
        "classification": {"description": "Debenture Fixed and Floating Charge"},
        "persons_entitled": [{"name": "Barclays Bank PLC"}]
    }
    sigs_full = adapter._parse_charge_into_signals(crn, full_chg)
    assert len(sigs_full) == 1
    assert sigs_full[0]["value_numeric"] == 1.0

    # Partial scope
    part_chg = {
        "id": "chg-var-002",
        "status": "partially-satisfied",
        "satisfied_on": "2026-08-05",
        "classification": {"description": "Legal Mortgage"},
        "persons_entitled": [{"name": "HSBC UK Bank PLC"}]
    }
    sigs_part = adapter._parse_charge_into_signals(crn, part_chg)
    assert len(sigs_part) == 1
    assert sigs_part[0]["value_numeric"] == 0.5
