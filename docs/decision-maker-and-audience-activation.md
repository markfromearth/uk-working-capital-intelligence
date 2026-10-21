# Decision-Maker Intelligence & Validated Audience Activation

## 1. Executive Summary & Core Mandate

The **Decision-Maker Intelligence & Validated Audience Activation** module extends the UK Working-Capital Intelligence & Prospect Scoring Platform to identify business decision-makers associated with high-priority companies, validate professional identities and corporate roles, and support compliant, measurable B2B marketing activation.

The platform explicitly **prohibits unrestricted personal data scraping, covert social profiling, or targeting based on personal financial distress or vulnerability**. All data processing operates under strict UK GDPR principles (Lawfulness, Fairness, Transparency, Purpose Limitation, Data Minimisation, Accuracy, Storage Limitation, and Integrity/Confidentiality).

---

## 2. Architecture & Data Model

The module maintains an architectural separation between **Canonical Corporate Entities** (`company_registry`), **Individual Human Identities** (`person_identities`), and **Contextual Corporate Roles** (`company_officers`, `professional_role_profiles`).

```
+----------------------------+
|      CompanyRegistry       | (Authoritative UK CRN)
+--------------+-------------+
               |
               +-----------------------------------+
               | 1:N                               | 1:N
               v                                   v
+----------------------------+       +-------------------------------+
|       CompanyOfficer       |       |    ProfessionalRoleProfile    |
| (Statutory Appointments    |       | (Seniority, Authority Scope,  |
|  & PSC Control Tiers)      |       |  Verification Status, Gates)  |
+--------------+-------------+       +---------------+---------------+
               |                                     |
               | N:1                                 | N:1
               v                                     v
+------------------------------------------------------------+
|                       PersonIdentity                       |
| (Canonical human identity, GDPR Art 17 erasure controls,   |
|  GDPR Art 21 suppression flags, normalized names)          |
+------------------------------------------------------------+
                               |
                               | 1:N
                               v
+------------------------------------------------------------+
|                     AudienceActivation                     |
| (Documented campaign job, platform bounds, LIA ref,        |
|  status: pending_compliance_review -> approved -> active)  |
+------------------------------------------------------------+
```

### Key Entities
1. `PersonIdentity`: Canonical individual identity. Strictly decoupled from corporate profiles to enforce independent rights management (Art 17 Right to Erasure, Art 21 Objection).
2. `CompanyOfficer`: Official statutory officers and Persons with Significant Control (PSC) retrieved from statutory registers (Companies House). Preserves full historical integrity (resigned/ceased appointments).
3. `ProfessionalRoleProfile`: Validated mapping linking an individual to an operational role within a specific company. Contains match confidence, verification status (`unverified`, `candidate`, `validated`, `disputed`, `stale`), and staleness flags (>180 days).
4. `AudienceActivation`: Documented B2B campaign job enforcing mandatory compliance review, lawful basis justification, purpose limitation, and live suppression checks.
5. `SuppressionRecord`: Immutable audit registry of opted-out or suppressed identifiers (`person_id`, `crn`, `email`, `phone`).
6. `CampaignPerformanceMetric`: Performance tracking records separating top-of-funnel reach (spend, impressions, clicks) from qualification (conversations) and commercial conversion (funded volume).

---

## 3. Statutory Discovery & Relationship Disambiguation

Statutory discovery is powered by the official Companies House REST API (`/company/{crn}/officers` and `/company/{crn}/persons-with-significant-control`) with deterministic simulation fallbacks when operating in offline/demo mode.

### Operational Responsibilities vs Statutory Control
- **Statutory Officers**: Directors and Secretaries are recorded with explicit appointment dates, cessation dates, and active flags.
- **Persons with Significant Control (PSC)**: Parsed into quantified shareholding/voting tiers:
  - 25% to 50%
  - 50% to 75%
  - 75% to 100%
- **Ceased & Resigned Appointments**: Historical records are never discarded. Resigned officers remain in the historical ledger (`is_active = False`, `resigned_on` populated) to prevent historical leakage and detect stale/contradictory claims.
- **The Golden PSC Rule**: **A Person with Significant Control (PSC) is NEVER assumed to be the finance decision-maker.** A PSC may be an external institutional investor or passive shareholder. Operational finance decision-maker status (`is_finance_decision_maker = True`) is awarded only to active directors and executives with operational finance responsibility (CFO, Finance Director, Managing Director, CEO, Owner-Manager).

---

## 4. Multi-Signal Identity Resolution

Identity resolution links candidate professional mentions to canonical person identities and company records using multi-signal corroboration.

### The Name-Alone Rejection Rule
**A name match alone is insufficient for a validated identity match.**
- Baseline name similarity yields a maximum confidence of **0.40** (status: `candidate`).
- Promotion to `validated` requires a minimum score of **0.75** and at least two independent corroborating signals:
  1. **Signal A**: Name match (0.40 pts).
  2. **Signal B**: Active statutory appointment on Companies House (0.35 pts).
  3. **Signal C**: Corporate domain email, official website citation, or regulatory filing (0.25 pts).

### Staleness & Contradiction Routing
- **Staleness Protocol**: Any profile without fresh verification for more than **180 days** is automatically routed to `stale` and fails data freshness gates.
- **Contradiction Detection**: If candidate profile claims current employment while statutory records show the officer has resigned or ceased control, the profile is immediately routed to `disputed` with documented contradictory evidence.

---

## 5. Five-Dimension Fail-Closed Validation Gate

Before any decision-maker can be included in an audience activation or sales outreach, they must pass the **DecisionMakerValidationGate**. The evaluation evaluates 5 independent dimensions and **fails closed**:

$$\text{Gate Passed} = G_1 \land G_2 \land G_3 \land G_4 \land G_5$$

| Gate Dimension | Requirement | Threshold | Fail-Closed Rationale |
|---|---|---|---|
| **Gate 1: Company Opportunity Confidence** | Company exhibits verified working-capital requirement | $P(\text{Opp}) \ge 0.50$ or Score $\ge 50.0$ | Prevents outreach to companies with no funding need. |
| **Gate 2: Person Identity Confidence** | Multi-signal corroborated professional identity | Status = `validated`, Confidence $\ge 0.75$ | Prevents contacting the wrong individual. |
| **Gate 3: Decision-Maker Relevance** | Qualified finance decision-maker | `is_finance_decision_maker = True` | Prevents misdirected outreach to non-buyers or passive PSCs. |
| **Gate 4: Data Freshness** | Active tenure, verified within retention window | Age $\le 180$ days, `is_current = True` | Prevents acting on obsolete personnel records. |
| **Gate 5: Marketing Eligibility** | Not suppressed, erased, or opted-out | `is_suppressed = False`, `is_erased = False` | Guarantees compliance with GDPR Art 17 and Art 21. |

> [!CRITICAL]
> **Validation Gate Independence Rule**: A high company opportunity confidence or extreme liquidity strain score **must NEVER compensate** for an unverified person identity, non-finance role, stale record, or missing marketing permission. If any individual dimension fails, the entire gate fails closed (`can_activate_audience = False`).

---

## 6. Audience Activation & Compliance Safeguards

Audience activations transform intelligence into compliant B2B execution.

### Permitted B2B Destinations
- **LinkedIn Company Targeting** (Account-level matching).
- **Platform ABM Native** (Platform-bound audience activation).
- **Direct Broker Outreach** (Telephone / Email qualification by authorised brokers).
- **First-Party CRM Sync** (Export to approved internal broker CRM).

### Fail-Closed Compliance Approval Workflow
1. **Creation**: New audience activations default to `pending_compliance_review`.
2. **Prohibition of Vulnerable / Distress Targeting**:
   Any campaign whose name, purpose, or creative copy contains references to *distress, insolvency, winding up, court judgments, bad debt, liquidation, or bankruptcy* is **automatically rejected at submission (HTTP 400)**.
3. **Dual Signoff**: Compliance officers or administrators must document formal review and verify the Legitimate Interest Assessment (`LIA-REF`).
4. **Instant Execution-Time Suppression Re-Check**:
   When an approved activation is executed (`/api/v1/audiences/{id}/execute`), the system re-evaluates the live suppression registry at runtime. If the person or company opted out between approval and execution, execution is blocked immediately.

---

## 7. GDPR Rights Enforcement

### Article 21: Right to Object / Suppression
- The suppression registry (`suppression_records`) records objections against `person_id`, `crn`, `email`, or `phone`.
- Active suppression permanently prevents inclusion in any existing or future audience activation.

### Article 17: Right to Erasure ("To Be Forgotten")
- Invoking erasure (`POST /api/v1/decision-makers/privacy/erasure`) scrubs personal identifiable data:
  - `full_name` $\rightarrow$ `[REDACTED ERASURE ART 17]`
  - `normalized_name` $\rightarrow$ `[redacted]`
  - `birth_month`, `birth_year`, `nationality` $\rightarrow$ `NULL`
  - Associated `professional_role_profiles` $\rightarrow$ Deleted
  - Associated statutory records $\rightarrow$ Disassociated (`person_id = NULL`) while preserving corporate statutory filing integrity.

---

## 8. Value-of-Information (EVOI) Integration

The **ValueOfInformationEngine** incorporates decision-maker discovery into its economic optimisation framework:
- If a company's commercial opportunity is unverified or has low liquidity strain ($P(\text{Opp}) < 0.50$), the investigation engine yields **`STOP`** on person discovery tools (`companies_house_officers_lookup`, `companies_house_psc_lookup`).
- **Privacy & Budget Protection**: Personal data is never queried, harvested, or processed for accounts that do not represent a qualified financing opportunity.

---

## 9. Campaign Measurement & Outcome Learning

The platform distinguishes top-of-funnel ad delivery from true commercial intent:
- Impressions, clicks, and spend are recorded as performance metrics, but are **never equated with qualification or intent**.
- Conversions are tracked through downstream broker milestones:
  - `qualified_conversations`
  - `facilities_funded_count`
  - `funded_volume_gbp`
- Funded deals and verified facility outcomes feed directly back into the continuous outcome learning loop for ML model retraining.

---

## 10. API Specification Reference

| Method | Endpoint | Description | Auth / RBAC |
|---|---|---|---|
| `GET` | `/api/v1/decision-makers/company/{crn}/officers` | List statutory officers & PSCs | Broker / Admin |
| `POST` | `/api/v1/decision-makers/company/{crn}/discover` | Trigger statutory officer & PSC discovery | Broker / Admin |
| `GET` | `/api/v1/decision-makers/company/{crn}/profiles` | List professional profiles with gate results | Broker / Admin |
| `GET` | `/api/v1/decision-makers/company/{crn}/profiles/{id}/gates` | Detailed 5-dimension gate evaluation | Broker / Admin |
| `POST` | `/api/v1/decision-makers/profiles/{id}/verify` | Manual identity validation promotion | Broker / Admin |
| `POST` | `/api/v1/decision-makers/suppression/objection` | Record GDPR Art 21 objection | Public / Broker |
| `POST` | `/api/v1/decision-makers/privacy/erasure` | Execute GDPR Art 17 data erasure | Admin / Compliance |
| `GET` | `/api/v1/audiences` | List audience activations & KPI summaries | Broker / Admin |
| `POST` | `/api/v1/audiences` | Create audience activation request | Broker / Admin |
| `GET` | `/api/v1/audiences/{id}` | Retrieve activation details & metrics | Broker / Admin |
| `POST` | `/api/v1/audiences/{id}/review` | Compliance officer review & approval | Admin / Compliance |
| `POST` | `/api/v1/audiences/{id}/execute` | Execute activation to target destination | Admin / Compliance |
| `POST` | `/api/v1/audiences/{id}/metrics` | Record campaign performance metrics | Broker / Admin |
