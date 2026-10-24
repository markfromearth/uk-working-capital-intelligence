# CH-Only Invoice Finance Warden (MVP Architectural Blueprint)

## 1. Executive Summary & Problem Scope
The **Invoice Finance Warden (CH-Only MVP)** transforms raw, public Companies House (CH) statutory data into qualified, pre-sized commercial invoice finance leads. It eliminates manual searches through mortgage registers, filing histories, and PDF balance sheets.

The MVP focuses on **Stage 1 (Point 1: Structured Screening & Provisional Scoring)** and **Stage 2 (Point 4: Dual-Path Accounts Extraction & Trapped Cash Sizing)**.

---

## 2. System Topology & Data Flow

```
                      [ Companies House Public APIs ]
                        • REST API (Profiles, Charges, Filings, Officers)
                        • Document API (Accounts XHTML & Scanned PDFs)
                        • Streaming API (Real-time Filings/Charges Stream)
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: THE GATEKEEPER (Structured Screener & Provisional Scorer)          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Canonical CRN Normalizer (SC, NI, OC support)                            │
│ 2. CompaniesHouseCHClient: Token-bucket rate limiter (1.8 req/sec max)      │
│ 3. Indicator Extraction Engine:                                             │
│    • B2B Sector Eligibility (SIC codes: Manufacturing, Wholesale, Transport)│
│    • White Space Detection (Legacy bank charge satisfied, book debts free)   │
│    • Refinancing Window Detection (Secondary funder debenture aged 24-48m)  │
│    • TM01 Director Resignation Clusters (>= 2 exits in 90 days)             │
│    • Administrative Delinquency (Overdue accounts/CS01)                     │
│ 4. Hard Knockout Evaluator (GAZ1 strike-off, active liquidation)            │
│ 5. Dynamic 100-Point Provisional Scorer (Configurable weights & archetypes) │
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
│    • Formulates commercial outreach rationale & sales pitch                 │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LEAD DISCOVERY DOSSIER & REST API SURFACE                                   │
│ • POST /api/v1/warden/screen/{crn}                                          │
│ • GET  /api/v1/warden/prospects                                             │
│ • GET  /api/v1/warden/prospects/{crn}/dossier                               │
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

class IndicatorBreakdown(BaseModel):
    indicator: str
    weight: float
    awarded: float
    triggered: bool
    evidence: str

class ScreeningResult(BaseModel):
    crn: str
    company_name: str
    sic_codes: List[str]
    is_b2b_eligible: bool
    provisional_score: float
    passed_gate: bool
    knockout_applied: bool
    knockout_reason: Optional[str] = None
    audit_trail: List[IndicatorBreakdown]

class BalanceSheetData(BaseModel):
    extraction_path: str  # "IXBRL" or "VISION_PDF"
    accounting_framework: str  # "FRS_105" or "FRS_102"
    period_end_date: Optional[str]
    current_assets_total: float
    debtors: Optional[float]
    cash_at_bank: Optional[float]
    creditors_due_within_one_year: float
    net_current_assets: float
    is_arithmetic_valid: bool
    rounding_variance: float

class TrappedCashDossier(BaseModel):
    crn: str
    company_name: str
    provisional_score: float
    balance_sheet: BalanceSheetData
    eligible_book_debts: float
    indicative_facility_80pct: float
    trapped_cash_release: float
    commercial_pitch: str
```

---

## 4. Indicator Mathematical Formulas & Temporal Decay

### 4.1 "White Space" (Unencumbered Book Debts)
Triggered when an entity has previously granted a floating charge/debenture to a High Street Bank, which has been satisfied with no new debenture registered.
$$\text{Age} = \text{Current Date} - \text{Latest Charge Satisfaction Date (days)}$$
$$\text{Eligibility Window}: 180 \le \text{Age} \le 730$$
$$\text{Score} = \text{Base Weight} \times e^{-0.693 \times (\text{Age} - 180) / 365}$$

### 4.2 "Refinancing Window" (Subprime Displacement)
Triggered when an entity has an active charge with a `SECONDARY_INVOICE_FINANCIER` or `ALTERNATIVE_CHALLENGER`:
$$\text{Charge Age Months} = \frac{\text{Current Date} - \text{Charge Creation Date}}{30.44}$$
$$\text{Active Window}: 24 \le \text{Charge Age Months} \le 48$$
During this window, borrowers have seasoned their credit and are prime candidates to refinance from 3–5% over base to prime rates (1.5–2.5% over base).

### 4.3 "TM01 Resignation Clusters"
$$\text{TM01 Count in 90 Days} \ge 2 \implies \text{Active Cluster Triggered}$$

### 4.4 "Administrative Delinquency vs Hard Knockouts"
- **Overdue Accounts / Confirmation Statement**: Mild operational stress (+10 points for Turnaround profile; flagged as monitoring for Prime).
- **First Gazette Notice (`GAZ1`)**:
  - `PRIME_INVOICE_DISCOUNTING`: Hard knockout ($\text{Score} \to 0$, `passed_gate = False`).
  - `TURNAROUND_SPECIAL_SITUATIONS`: Permitted, high risk indicator.

---

## 5. Risk & Mitigation Strategy

| Failure Mode | Root Cause | Architectural Mitigation |
| :--- | :--- | :--- |
| **S3 400 Bad Request** | Auth header forwarded across redirect | Custom `httpx.AsyncClient` redirect hook removing `Authorization` on S3 domains |
| **CH 429 Rate Limit** | Over-calling CH REST API | In-memory token-bucket limiter (1.8 req/sec) + jittered exponential backoff |
| **FRS 105 Missing Debtors** | Micro-entity accounts only state Current Assets | Sector-specific fallback calculation with explicit transparency flag |
| **Hallucinated PDF Math** | LLM parsing errors on balance sheet | Deterministic Python verification rule ($\pm £2$ tolerance) before dossier generation |
| **Non-B2B Wasted Compute** | Scraping retail/consumer businesses | Upstream SIC code filter discarding non-invoice finance sectors |
