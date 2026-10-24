"""
Pydantic schemas and contracts for the Companies House Invoice Finance Warden.
Defines data structures for Stage 1 (Screening & Scoring), Stage 2 (Accounts Extraction & Trapped Cash),
and Stage 3 (Post-Gate Intelligence Refinement Agent & Retrievable Primary Sources).
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class LenderClassification(str, Enum):
    HIGH_STREET_BANK = "HIGH_STREET_BANK"
    SECONDARY_INVOICE_FINANCIER = "SECONDARY_INVOICE_FINANCIER"
    ALTERNATIVE_CHALLENGER = "ALTERNATIVE_CHALLENGER"
    UNCLASSIFIED = "UNCLASSIFIED"


class LendingArchetype(str, Enum):
    PRIME_INVOICE_DISCOUNTING = "PRIME_INVOICE_DISCOUNTING"
    TURNAROUND_SPECIAL_SITUATIONS = "TURNAROUND_SPECIAL_SITUATIONS"


class PrimarySourceReference(BaseModel):
    """Retrievable primary evidence source."""
    source_type: str  # "COMPANIES_HOUSE_CHARGES", "COMPANIES_HOUSE_FILING", "ACCOUNTS_DOCUMENT", "GAZETTE_NOTICE", "PUBLIC_REPORT"
    source_id: str
    title: str
    uri: str
    retrieval_method: str = "REST_API"  # "REST_API", "DOCUMENT_API", "PUBLIC_WEB"
    published_date: Optional[str] = None
    excerpt: Optional[str] = None


class ScoreRefinementAdjustment(BaseModel):
    """Detailed justification for an increase or decrease in the probability score."""
    metric_name: str
    delta_points: float  # Positive for score increase, negative for decrease
    direction: str  # "INCREASE" or "DECREASE"
    justification: str
    primary_sources: List[PrimarySourceReference] = Field(default_factory=list)


class IndicatorBreakdown(BaseModel):
    indicator: str
    weight: float
    awarded: float
    triggered: bool
    evidence: str
    primary_source: Optional[PrimarySourceReference] = None


class ScreeningResult(BaseModel):
    crn: str
    company_name: str
    sic_codes: List[str] = Field(default_factory=list)
    is_b2b_eligible: bool
    provisional_score: float
    passed_gate: bool
    knockout_applied: bool
    knockout_reason: Optional[str] = None
    audit_trail: List[IndicatorBreakdown] = Field(default_factory=list)
    primary_sources: List[PrimarySourceReference] = Field(default_factory=list)


class BalanceSheetData(BaseModel):
    extraction_path: str  # "IXBRL" or "VISION_PDF"
    accounting_framework: str  # "FRS_105" or "FRS_102"
    period_end_date: Optional[str] = None
    current_assets_total: float
    debtors: Optional[float] = None
    cash_at_bank: Optional[float] = None
    creditors_due_within_one_year: float
    net_current_assets: float
    is_arithmetic_valid: bool
    rounding_variance: float = 0.0
    primary_source: Optional[PrimarySourceReference] = None


class TrappedCashDossier(BaseModel):
    crn: str
    company_name: str
    provisional_score: float
    refined_score: float = 0.0
    score_refinement_delta: float = 0.0
    balance_sheet: BalanceSheetData
    eligible_book_debts: float
    indicative_facility_80pct: float
    trapped_cash_release: float
    commercial_pitch: str
    refinements: List[ScoreRefinementAdjustment] = Field(default_factory=list)
    primary_sources: List[PrimarySourceReference] = Field(default_factory=list)
