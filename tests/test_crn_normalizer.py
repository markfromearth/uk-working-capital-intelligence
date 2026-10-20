import pytest
from backend.app.core.crn_normalizer import normalize_uk_crn, is_valid_uk_crn

def test_normalize_standard_numeric_crn():
    # 8-digit exact
    norm, valid, err = normalize_uk_crn("08931201")
    assert valid is True
    assert norm == "08931201"
    assert err is None

    # 7-digit numeric missing leading zero
    norm, valid, err = normalize_uk_crn("8931201")
    assert valid is True
    assert norm == "08931201"

    # Short numeric padded to 8 digits
    norm, valid, err = normalize_uk_crn("12345")
    assert valid is True
    assert norm == "00012345"

def test_normalize_scotland_prefix():
    # SC + 6 digits
    norm, valid, err = normalize_uk_crn("SC123456")
    assert valid is True
    assert norm == "SC123456"

    # SC with whitespace/hyphens and lowercase
    norm, valid, err = normalize_uk_crn(" sc-123456 ")
    assert valid is True
    assert norm == "SC123456"

    # SC with fewer digits padded to 6
    norm, valid, err = normalize_uk_crn("SC1234")
    assert valid is True
    assert norm == "SC001234"

def test_normalize_northern_ireland_and_llp():
    # NI
    norm, valid, _ = normalize_uk_crn("NI654321")
    assert valid is True
    assert norm == "NI654321"

    # OC (LLP England & Wales)
    norm, valid, _ = normalize_uk_crn("OC300123")
    assert valid is True
    assert norm == "OC300123"

def test_invalid_crn_handling():
    # Empty string
    norm, valid, err = normalize_uk_crn("")
    assert valid is False
    assert norm is None
    assert "empty" in err.lower()

    # None
    norm, valid, err = normalize_uk_crn(None)
    assert valid is False

    # Exceeding 8 digits
    norm, valid, err = normalize_uk_crn("123456789")
    assert valid is False
    assert "exceeds" in err.lower()

    # Unrecognized prefix
    norm, valid, err = normalize_uk_crn("ZZ123456")
    assert valid is False
    assert "unrecognized" in err.lower()

    # Non-numeric suffix for alpha prefix
    norm, valid, err = normalize_uk_crn("SC12AB56")
    assert valid is False

def test_is_valid_uk_crn_convenience():
    assert is_valid_uk_crn("01234567") is True
    assert is_valid_uk_crn("SC123456") is True
    assert is_valid_uk_crn("INVALID") is False
