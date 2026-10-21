# Provider Integration Matrix & Boundary Documentation

This document defines the interface specifications, operational boundaries, rate limits, authentication mechanisms, failure models, and current credential readiness for all external data providers integrated into the UK Working-Capital Intelligence & Prospect Scoring Platform.

---

## 1. Provider Integration Inventory

| Provider | Ingestion Channel | Auth Method | Rate Limits & Quotas | Boundary Status | Fixture / Live Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Companies House REST API** | HTTPS REST (`GET /company/{crn}/charges`, `GET /company/{crn}/filing-history`) | HTTP Basic (`API_KEY:` as base64) | 600 requests per 5-minute sliding window | Implemented & Verified | Live-ready (Key configurable in `COMPANIES_HOUSE_API_KEY`) |
| **Companies House Streaming API** | Long-polling HTTP chunked stream (`/charges-stream`) | HTTP Basic (`API_KEY:` as base64) | 1 concurrent connection per API key | Implemented with resilient recovery & 416 fallback | Resilient cursor simulation verified (`test_companies_house_stream_resilience.py`) |
| **Creditsafe Business API** | HTTPS REST / Webhook (`POST /v1/companies`, Webhook push) | OAuth2 Bearer / Webhook HMAC SHA-256 (`X-Creditsafe-Signature`) | 100 req/sec commercial tier | Implemented & Verified | Deterministic Fixtures & Webhook replay protection verified |
| **Experian Commercial API** | HTTPS SOAP/REST (`POST /v2/credit-risk/commercial`) | OAuth2 Client Credentials / Mututal TLS (mTLS) | Account-dependent SLA | Implemented behind adapter abstraction | Deterministic Fixtures |
| **Workforce Dynamics (Proxycurl / Job Boards)** | HTTPS REST (`GET /v1/company/headcount-trends`) | Bearer API Token (`X-Workforce-Key`) | 300 requests/minute | Implemented behind adapter abstraction | Deterministic Fixtures |
| **Anthropic Claude 3.5 Sonnet / LLM** | HTTPS REST (`POST /v1/messages`) | Header API Key (`x-api-key`) | 50 req/min, 40,000 TPM | Implemented with strict grounding validation | Grounded deterministic fallback active |

---

## 2. Ingestion Contracts and Payload Normalisation

All external providers normalize their data payloads into the canonical [`RawSignalPayload`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/schemas/signal.py) schema before ingestion into the data lake and feature store.

```
                    ┌────────────────────────────┐
                    │ Raw Provider Ingestion     │
                    │ (Companies House, Creditsafe)
                    └─────────────┬──────────────┘
                                  │ Normalisation
                                  ▼
                    ┌────────────────────────────┐
                    │ Canonical RawSignalPayload │
                    │ - raw_event_id             │
                    │ - source_provider          │
                    │ - event_type               │
                    │ - observation_timestamp    │
                    │ - payload_hash (SHA-256)   │
                    └─────────────┬──────────────┘
                                  │ Ingestion Service
                                  ▼
                    ┌────────────────────────────┐
                    │ Postgres/SQLite RawSignal  │
                    │ Store with Bi-temporal TS  │
                    └────────────────────────────┘
```

### A. Companies House Corporate Filings Adapter
- **Module**: [`backend/app/providers/companies_house.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/providers/companies_house.py)
- **Filing Form Handling**:
  - `MR04`: Statement of satisfaction in full or in part of a mortgage or charge.
  - `MR01` / `MR02`: Creation of a charge / debenture.
  - `MR05`: Statement of partial satisfaction / release of property from charge.
- **Interpretation Logic**:
  - `MR04` forms are parsed to determine whether satisfaction is `FULL` or `PARTIAL`.
  - A satisfied charge frees unencumbered assets, signaling potential capacity for alternative working-capital facilities (e.g. invoice discounting, asset refinance). It is treated as an investigative signal, not a confirmed sales lead.
- **Stream Resilience**:
  - Long-lived cursor timepoints are tracked in [`StreamingCheckpoint`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/signal.py).
  - Handles HTTP 416 (Requested Range Not Satisfiable / Timepoint Expired) by resetting the cursor to `stream_head` and logging a warning.

### B. Creditsafe Commercial Credit Adapter
- **Module**: [`backend/app/providers/creditsafe.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/providers/creditsafe.py)
- **Webhook Security & Replay Prevention**:
  - Signature verification: `HMAC-SHA256(secret, body)` against header `X-Creditsafe-Signature`.
  - Replay defense: `X-Creditsafe-Timestamp` must be within 300 seconds of ingestion server clock.
- **Metrics Normalised**:
  - `Days Beyond Terms (DBT)`: Actual trade payment delinquency.
  - `Commercial Credit Score`: Integer 1-100 rating.
  - `CCJ Events`: Court judgments with registration date and monetary amount.

### C. Experian Commercial Adapter
- **Module**: [`backend/app/providers/experian.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/providers/experian.py)
- **Metrics Normalised**:
  - `Commercial Risk Delphi Band` (Band 1 lowest risk to Band 6 maximum risk).
  - `Commercial Credit Limit Recommendations`.
  - `Days Sales Outstanding (DSO)`: Distinct from DBT; captures internal sales cycle duration.

### D. Workforce Dynamics Adapter
- **Module**: [`backend/app/providers/workforce.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/providers/workforce.py)
- **Metrics Normalised**:
  - `headcount_total`: Total detected employees.
  - `headcount_delta_pct_90d`: Percentage change in staffing over 90 days.
  - `open_job_postings_delta_pct`: Contracting recruitment volumes indicates defensive cost reductions.

---

## 3. Resilient Ingestion & Dead-Letter Recovery (DLQ)

When an incoming payload contains malformed schema, unresolvable company identifiers, or invalid signatures, it is rejected and quarantined into the dead-letter queue:

1. **Quarantine Table**: [`DeadLetterEvent`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/signal.py).
2. **Quarantine Reason**: Recorded alongside payload raw JSON, error stack trace, and source IP.
3. **Recovery Endpoint**: `POST /api/v1/ingest/dead-letter-queue/{id}/replay`
4. **DLQ Replay Function**: [`replay_dead_letter_event`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/ingestion.py)
   - Re-extracts payload.
   - Re-attempts normalisation and entity resolution.
   - Sets `resolved = True` upon successful ingestion.

---

## 4. Live vs Simulated Production Readiness

> [!IMPORTANT]
> The platform is architected with clear boundaries between tested local simulation fixtures and production external credentials.

| Subsystem | Staging / Test Status | Production Deployment Prerequisite |
| :--- | :--- | :--- |
| Companies House REST | Live-ready (Free official API key) | Set `COMPANIES_HOUSE_API_KEY` in environment |
| Companies House Stream | Simulator verified | Dedicated egress IP & stream connection monitoring |
| Creditsafe Webhook | HMAC & timestamp verified | Contracted Creditsafe Connect subscription & Webhook endpoint URL registered |
| Experian API | Simulator verified | Signed Experian API bilateral agreement & mTLS client certificates installed |
| Workforce Trends | Deterministic fixtures verified | Active Proxycurl or LinkUp data license |
| LLM Synthesis | Grounded fallback verified | Anthropic API key (`ANTHROPIC_API_KEY`) provisioned |
