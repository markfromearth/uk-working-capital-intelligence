"""
UK Company Registration Number (CRN) Normalization and Validation.

Companies House CRN formats:
- Standard England/Wales companies: 8 digits (often written without leading zeros in informal records, e.g. "1234567" -> "01234567")
- Scotland: 'SC' followed by 6 digits (e.g. 'SC123456')
- Northern Ireland: 'NI' followed by 6 digits (e.g. 'NI123456')
- Limited Liability Partnerships: 'OC' (England/Wales), 'SO' (Scotland), 'NC' (NI) followed by 6 digits
- Other recognized prefixes: 'LP', 'SL', 'NL', 'FC', 'NF', 'SF', 'RS', 'RC', 'IC', 'SI', 'NP', 'NO', 'SP', 'SZ'
"""

import re
from typing import Optional, Tuple

# Valid 2-character prefix types recognized by Companies House
RECOGNIZED_PREFIXES = {
    "SC", "NI", "OC", "SO", "NC", "LP", "SL", "NL", "FC", "NF", "SF", 
    "RS", "RC", "IC", "SI", "NP", "NO", "SP", "SZ", "AC", "ZC", "SA", 
    "NA", "RO", "SE", "GS"
}

# Regex for normalized standard 8-digit numeric or 2-alpha + 6-digit alphanumeric
CRN_NORMALIZED_REGEX = re.compile(r"^([A-Z]{2}\d{6}|\d{8})$")

def normalize_uk_crn(raw_crn: Optional[str]) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Normalizes a raw UK Company Registration Number.
    
    Returns:
        (normalized_crn, is_valid, error_reason)
        
    Examples:
        "1234567"   -> ("01234567", True, None)
        "SC12345"   -> ("SC012345", True, None) (padded to 6 digits)
        " ni 123456" -> ("NI123456", True, None)
        "08931201"  -> ("08931201", True, None)
        "INVALID"   -> (None, False, "Invalid CRN format")
    """
    if not raw_crn:
        return None, False, "CRN is empty or None"
    
    # Clean whitespace and hyphens, convert to uppercase
    cleaned = re.sub(r"[\s\-_./]", "", str(raw_crn).strip().upper())
    
    if not cleaned:
        return None, False, "CRN contains only whitespace/delimiters"
    
    # Case 1: Purely digits
    if cleaned.isdigit():
        if len(cleaned) <= 8:
            normalized = cleaned.zfill(8)
            return normalized, True, None
        else:
            return None, False, f"Numeric CRN exceeds 8 digits: {cleaned}"
            
    # Case 2: 2 alpha characters followed by digits
    if len(cleaned) >= 3 and cleaned[:2].isalpha():
        prefix = cleaned[:2]
        digits = cleaned[2:]
        
        if not digits.isdigit():
            return None, False, f"CRN alphanumeric suffix must be numeric: {cleaned}"
            
        if prefix not in RECOGNIZED_PREFIXES:
            return None, False, f"Unrecognized Companies House prefix: {prefix}"
            
        if len(digits) <= 6:
            normalized = f"{prefix}{digits.zfill(6)}"
            return normalized, True, None
        else:
            return None, False, f"Alpha-prefixed CRN digits exceed 6: {cleaned}"
            
    # Case 3: Other unrecognized formats (e.g. sole trader or foreign format without recognized prefix)
    return None, False, f"Invalid UK CRN structure: {cleaned}"


def is_valid_uk_crn(crn: Optional[str]) -> bool:
    """Convenience boolean checker for a valid normalized or normalizable CRN."""
    norm, valid, _ = normalize_uk_crn(crn)
    return valid and norm is not None
