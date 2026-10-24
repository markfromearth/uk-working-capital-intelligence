# CH-Only Invoice Finance Warden (MVP Architectural Blueprint)

## 1. Executive Summary & Problem Scope
The **Invoice Finance Warden (CH-Only MVP)** transforms raw, public Companies House (CH) statutory data into qualified, pre-sized commercial invoice finance leads. It eliminates manual searches through mortgage registers, filing histories, and PDF balance sheets.

The Warden focuses specifically on **surfacing key indicators that an invoice finance service may be of value and interest to a company within the short to mid term** (current financial year or near future).

The operational pipeline consists of:
1. **Stage 1 (Point 1: Structured Screening & Provisional Scoring)**: Rapid filtering, White Space/Refinancing trigger detection, and gatekeeper scoring.
2. **Stage 2 (Point 4: Dual-Path Accounts Extraction & Trapped Cash Sizing)**: Deterministic iXBRL + Vision PDF extraction, math validation, and borrowing base calculation.
3. **Stage 3 (Post-Gate Handoff: Intelligence Refinement & Provenance Agent)**: Deep-dive score refinement adjusting the probability score up or down based on additional indicator metrics, business insights, reports, and news/filing context, while ensuring **all primary sources of indicators are directly retrievable**.

---

## 2. System Topology & Data Flow

```
                      [ Companies House Public APIs ]
                        • REST API (Profiles, Charges, Filings, Officers)
                        • Document API (Accounts XHTML & Scanned PDFs)
                        • Streaming API (Real-time Filings/Charges Stream)
                        • Gazette / Regulatory Public Notices
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: THE GATEKEEPER (Structured Screener & Provisional Scorer)          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Canonical CRN Normalizer (SC, NI, OC support)                            │
│ 2. CompaniesHouseCHClient: Token-bucket rate limiter (1.8 req/sec max)      │
│ 3. Indicator Extraction Engine (Short-to-Mid Term Value Triggers):          │
│    • B2B Sector Eligibility (SIC codes: Manufacturing, Wholesale, Transport)│
│    • White Space Detection (Legacy bank charge satisfied, book debts free)   │
│    • Refinancing Window Detection (Secondary funder debenture aged 24-48m)  │
│    • TM01 Director Resignation Clusters (>= 2 exits in 90 days)             │
│    • Administrative Delinquency (Overdue accounts/CS01)                     │
│ 4. Hard Knockout Evaluator (GAZ1 strike-off, active liquidation)            │
│ 5. Dynamic 100-Point Provisional Scorer (Configurable weights & archetypes) │
│ 6. Primary Source Tagging: Every indicator captures canonical source URI.   │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                         [ Provisional Score >= 65.0? ]
                                      │
                    ┌─────────────────┴─────────────────┐
               [ NO: Drop ]                       [ YES: Pass ]
           (Zero token/doc cost)                        │
                                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: THE QUANT EXTRACTOR (Dual-Path Financial Extraction & Sizing)      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Accounts Fetcher with Custom S3 Redirect Handler:                        │
│    • Follows 302/307 to Amazon S3 while STRIPPING the Authorization header   │
│ 2. Triage Router:                                                           │
│    • If application/xhtml+xml: Path A (Deterministic iXBRL Parser)          │
│    • If application/pdf: Path B (PyMuPDF Page-Pruner + Vision LLM)           │
│ 3. Accounting Balance Sheet Extraction (FRS 105 Micro vs FRS 102 Small):    │
│    • Current Assets (Total), Debtors, Cash, Creditors < 1 Year, Net Assets  │
│ 4. Deterministic Python Accounting Reconciliation:                          │
│    • Identity: abs((Current Assets - Creditors < 1yr) - Net Assets) <= £2.0 │
│ 5. Trapped Cash & Borrowing Base Calculation:                               │
│    • Eligible Book Debts = Debtors (or sector-weighted Current Assets)      │
│    • Indicative Facility = Debtors * 80% Advance Rate                       │
│    • Primary Source Tagging: Exact statutory document link & page metadata. │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STAGE 3: POST-GATE HANDOFF — THE INTELLIGENCE REFINEMENT & PROVENANCE AGENT  │
├─────────────────────────────────────────────────────────────────────────────┤
│ Indicated businesses are handed off to this agent to refine the probability │
│ score to accurately indicate short-to-mid term financing potential:         │
│                                                                             │
│ 1. Surfacing Additional Indicator Metrics:                                  │
│    • Historical Trading Seasoning: Company longevity & stability factor.    │
│    • Working Capital Velocity: Ratio of Debtors to Creditors (< 1yr).       │
│    • Director Network Track Record: Number of active/dissolved co-entities. │
│ 2. Contextual Business Insights, Reports & Regulatory Notices:              │
│    • Cross-company directorship signals (serial directors in B2B).          │
│    • Published notices, gazette filings, and industry trend reports.        │
│ 3. Justified Probability Score Refinement (Up or Down):                     │
│    • Upward Adjustments (+pts): High debtor velocity, seasoned trading,     │
│      unbroken filing cadence, growing assets.                               │
│    • Downward Adjustments (-pts): High creditor overhang, recent debtor     │
│      contraction, excessive director churn.                                 │
│ 4. Retrievable Primary Sources:                                             │
│    • NON-NEGOTIABLE PROVENANCE: Every indicator, adjustment, and insight    │
│      contains retrievable primary source links (Companies House API URIs,   │
│      charge codes, document IDs, filing transaction IDs, Gazette notices).  │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ REFINED LEAD DISCOVERY DOSSIER & REST API SURFACE                           │
│ • POST /api/v1/warden/screen/{crn}                                          │
│ • GET  /api/v1/warden/prospects                                             │
│ • GET  /api/v1/warden/prospects/{crn}/dossier                               │
│ • GET  /api/v1/warden/prospects/{crn}/primary-sources                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Schema & Models

### 3.1 Pydantic Core Contracts (`backend/app/schemas/warden.py`)

```python
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class LenderClassification(str, Enum):
    HIGH_STREET_BANK = "HIGH_STREET_BANK"          # Barclays, NatWest, Lloyds, HSBC, Santander
    SECONDARY_INVOICE_FINANCIER = "SECONDARY_IF"  # Bibby, Ultimate, Time Finance, Novuna, Close Bros
    ALTERNATIVE_CHALLENGER = "ALTERNATIVE"        # iwoca, Funding Circle, MarketFinance
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
    retrieval_method: str  # "REST_API", "DOCUMENT_API", "PUBLIC_WEB"
    published_date: Optional[str] = None
    excerpt: Optional[str] = None

class ScoreRefinementAdjustment(BaseModel):
    """Detailed justification for an increase or decrease in probability score."""
    metric_name: str
    delta_points: float  # Positive for increase, negative for decrease
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
    sic_codes: List[str]
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
    refined_score: float
    score_refinement_delta: float
    balance_sheet: BalanceSheetData
    eligible_book_debts: float
    indicative_facility_80pct: float
    trapped_cash_release: float
    commercial_pitch: str
    refinements: List[ScoreRefinementAdjustment] = Field(default_factory=list)
    primary_sources: List[PrimarySourceReference] = Field(default_factory=list)
```

---

## 4. Retrievable Primary Sources & Score Refinement Engine

### 4.1 Primary Source Retrieval Protocol
Every single score calculation, whether from Stage 1 (Charges, Filings, SIC) or Stage 2 (Accounts) or Stage 3 (Refinement), binds to a `PrimarySourceReference` containing:
- `source_type`: Category of the statutory record.
- `source_id`: Specific filing transaction ID, charge code, or document hash.
- `uri`: Direct canonical endpoint or web link (e.g. `https://find-and-update.company-information.service.gov.uk/company/{crn}/charges/{charge_id}`).
- `excerpt`: The exact underlying text or numerical data extracted.

### 4.2 Score Refinement Mechanics
The post-gate **Intelligence Refinement Agent** takes the provisional score and modulates it up or down:
- **Upward Refinements (+pts)**:
  - Strong Debtor-to-Creditor Cover ($\text{Debtors} / \text{Creditors} > 1.4$): $+5.0$ pts.
  - Mature B2B Trading Stability ($> 5$ years continuous active incorporation with no gazette strikes): $+5.0$ pts.
  - Favorable Working Capital Velocity: $+3.0$ pts.
- **Downward Refinements (-pts)**:
  - High Creditor Overhang ($\text{Creditors} > \text{Current Assets}$): $-8.0$ pts.
  - Recent Director Resignation Turbulence (TM01 in preceding quarter): $-5.0$ pts.
  - Accounts filing near statutory delinquency deadline: $-3.0$ pts.

Every adjustment must bundle its supporting `PrimarySourceReference` records to ensure underwriters can audit the origin in one click.
