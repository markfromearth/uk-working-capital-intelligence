from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class CompanyBase(BaseModel):
    crn: str = Field(..., description="Canonical 8-char UK Company Registration Number")
    company_name: str
    company_status: Optional[str] = "active"
    company_type: Optional[str] = "ltd"
    incorporation_date: Optional[datetime] = None
    registered_office_address: Optional[str] = None
    sic_codes: Optional[str] = None
    website_domain: Optional[str] = None
    source_reference: Optional[str] = None
    is_synthetic: Optional[int] = 0

class CompanyCreate(CompanyBase):
    pass

class CompanyOut(CompanyBase):
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    liquidity_strain_score: Optional[float] = None
    score_band: Optional[str] = None
    primary_signals_summary: Optional[str] = None
    estimated_facility_size: Optional[float] = None
    eligible_book_debts: Optional[float] = None
    debtors_to_assets_ratio: Optional[int] = None
    charges_count: Optional[int] = None
    has_white_space: Optional[bool] = None
    working_capital_risk: Optional[str] = None

    model_config = {"from_attributes": True}

class CompanyMatchReviewOut(BaseModel):
    id: int
    source_identifier: str
    candidate_crn: Optional[str] = None
    candidate_name: Optional[str] = None
    source_provider: str
    match_score: float
    resolution_method: str
    status: str
    reviewer_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class MatchReviewDecision(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")
    reviewer_notes: Optional[str] = None
