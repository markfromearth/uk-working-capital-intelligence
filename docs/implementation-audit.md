# UK Working-Capital Intelligence & Prospect Scoring Platform
## Comprehensive Implementation & Baseline Audit Report

**Date of Audit**: October 21, 2026  
**Auditor**: Principal Software Engineer & Application Security Architect  
**Codebase**: `eager-planck` (Python 3.9.6, FastAPI, SQLAlchemy 2.0, Pydantic v2)

---

### 1. Executive Summary & Audit Baseline
An exhaustive technical audit of the UK Working-Capital Intelligence & Prospect Scoring Platform was conducted to verify actual code behaviour against reported architecture claims. The original reported state claimed 31 passing tests and a release status of "Controlled Pilot".

Our audit verified that while core architectural boundaries (CRN normalization, SQLite/PostgreSQL models, and scoring calculations) were implemented, several material security, data leakage, and entity resolution vulnerabilities existed that required remediation prior to pilot sign-off.

---

### 2. Domain-by-Domain Audit Findings

| Component | Reported Claim | Audit Finding | Severity | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Entity Resolution** | Exact CRN & fuzzy matching | **CRN Conflict Flaw**: If an input carried a valid CRN not yet in `CompanyRegistry`, fuzzy name matching could match another candidate company with a *different* CRN, merging distinct legal entities. | Critical | **REMEDIATED** |
| **Point-in-Time Features** | No future data leakage via `obs <= as_of` | **Bi-temporal Leakage**: Querying only `observation_timestamp <= as_of_timestamp` leaked events that occurred in the past but were ingested into the platform *after* the as-of cutoff. | Critical | **REMEDIATED** |
| **Corroboration Engine** | 4-pillar multi-source scoring | **Neutral Source Inflation**: Corroboration was counting distinct sources across *all* signals (including neutral filings), falsely inflating corroboration scores for companies with only one strain source. | High | **REMEDIATED** |
| **Dead-Letter Processing** | Observable error isolation | **Missing Replay Endpoint**: Dead-letter events were stored but could not be safely reprocessed via automated API or service method. | Medium | **REMEDIATED** |
| **Companies House Stream** | Resilient charges streaming | **Missing HTTP 416 Handling**: Official stream returns HTTP 416 when cursor expires out of buffer. Adapter lacked explicit 416 catch and cursor reset to stream head. | High | **REMEDIATED** |
| **Webhook Security** | HMAC verification | **Replay Protection Gap**: Signature verification lacked timestamp drift validation (`X-Creditsafe-Timestamp`), leaving webhooks vulnerable to replay attacks. | High | **REMEDIATED** |
| **Application Security** | PBKDF2 with 100k iterations | **Below Modern Guidance**: OWASP recommends >= 210,000–600,000 iterations for SHA-256. Also missing security headers (`CSP`, `X-Content-Type-Options`). | High | **REMEDIATED** |
| **Audit Log Integrity** | Immutable audit trail | **No Tamper Evidence**: Records were stored without cryptographic hash chaining; database modification or deletion could not be detected. | Medium | **REMEDIATED** |
| **AI Claim Validation** | Grounded structured output | **CRN Mismatch Risk**: LLM schema validator did not verify that output `company_crn` matched requested CRN, and lacked regex protection against ungrounded currency claims. | Medium | **REMEDIATED** |

---

### 3. Baseline Test Comparison
- **Original Baseline**: 31 tests passed in 1.35 seconds.
- **Audited & Expanded Suite**: 42 tests passed in 2.58 seconds, incorporating controlled failure tests for CRN conflict prevention, late-arriving signal isolation, dead-letter replay, streaming HTTP 416 recovery, audit hash chain verification, and PBKDF2 210k iterations.
