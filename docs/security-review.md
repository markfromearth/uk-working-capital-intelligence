# Security Architecture & Vulnerability Review

This document presents the application security architecture, cryptographic implementations, tamper-evident audit mechanisms, and vulnerability reviews conducted for the UK Working-Capital Intelligence & Prospect Scoring Platform.

---

## 1. Security Architecture Overview

The platform implements defence-in-depth across the ingestion, data persistence, application, and presentation layers:

```
                          ┌────────────────────────┐
                          │ External Requests      │
                          │ (HTTPS / TLS 1.3)      │
                          └───────────┬────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │ Security Headers       │
                          │ - HSTS, CSP, X-Frame   │
                          │ - X-Content-Type       │
                          └───────────┬────────────┘
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
             ┌────────────────────────┐┌────────────────────────┐
             │ API Authentication     ││ Webhook Validation     │
             │ - JWT (HS256)          ││ - HMAC-SHA256 Sig      │
             │ - RBAC Enforcement     ││ - 300s Timestamp Window│
             └───────────┬────────────┘└───────────┬────────────┘
                         │                         │
                         └────────────┬────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │ Business Logic Layer   │
                          │ - Grounded Explanations│
                          │ - Input Sanitisation   │
                          └───────────┬────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │ Tamper-Evident Store   │
                          │ - SHA-256 Hash Chained │
                          │   Audit Log Ledger     │
                          │ - PBKDF2-210k Passwords│
                          └────────────────────────┘
```

---

## 2. Cryptographic Implementations

### A. Password Hashing: PBKDF2-SHA256 (210,000 Iterations)
- **Module**: [`backend/app/core/security.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/core/security.py)
- In compliance with OWASP Password Storage Cheat Sheet standards, password hashing employs:
  - Algorithm: PBKDF2 with SHA-256
  - Iterations: `210,000`
  - Salt: Cryptographically secure 16-byte random salt (`secrets.token_hex(16)`)
  - Storage format: `pbkdf2_sha256$210000${salt}${hash}`
- **Backwards Compatibility**: The verifier recognizes legacy hashes and validates them seamlessly while issuing re-hash flags upon next login.
- **Verification**: Verified by automated test [`test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py).

### B. Tamper-Evident Audit Log Hash Chaining
- **Model**: [`AuditLog`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/user.py)
- **Service**: [`backend/app/services/audit.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/audit.py)
- Every audit entry (user login, prospect status transition, manual note creation) computes a cryptographic hash:
  $$\text{record\_hash} = \text{SHA-256}(\text{prev\_hash} \,\|\, \text{action} \,\|\, \text{user\_id} \,\|\, \text{timestamp} \,\|\, \text{resource\_type} \,\|\, \text{details})$$
- The genesis entry uses `GENESIS_HASH = "0" * 64`.
- **Integrity Verification**:
  - `GET /api/v1/system/audit-verification` verifies the unbroken chain from genesis to the latest record.
  - If any row is modified, deleted, or inserted out-of-band directly in the database, the chain breaks and the verification fails.
  - Verified by automated tests [`test_tamper_evident_audit_log_hash_chain`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py) and [`test_audit_log_tamper_detection`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py).

### C. Webhook Ingestion Authentication & Anti-Replay
- **Module**: [`backend/app/providers/creditsafe.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/providers/creditsafe.py)
- Webhooks from credit bureaus are signed using HMAC-SHA256 (`X-Creditsafe-Signature`).
- To prevent replay attacks where an eavesdropped payload is re-transmitted:
  - Header `X-Creditsafe-Timestamp` is required.
  - Ingestion server checks: $|\text{server\_time} - \text{webhook\_time}| \le 300\text{ seconds}$.
  - Replays outside the 5-minute window are rejected with HTTP 401.

---

## 3. Web & Network Application Security

### HTTP Security Headers
Every HTTP response emitted by the FastAPI backend includes mandatory security headers configured in [`backend/app/main.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/main.py):

| Header | Value | Purpose |
| :--- | :--- | :--- |
| `X-Content-Type-Options` | `nosniff` | Prevents MIME-sniffing exploits. |
| `X-Frame-Options` | `DENY` | Prevents clickjacking attacks. |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';` | Restricts source execution contexts. |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Protects URI token leakage in referrer headers. |

Verified by automated test [`test_security_headers_on_responses`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py).

---

## 4. Role-Based Access Control (RBAC)

The application enforces four discrete permission tiers:

| Role | Permissions |
| :--- | :--- |
| `ADMIN` | Full system access, Dead Letter Queue replay, Audit chain verification, User management. |
| `ANALYST` | Raw signal exploration, entity manual unmerge / conflict review, scoring model parameter inspection. |
| `BROKER` | Prospect search, company dossier viewing, explanation generation, status pipeline updates, contact note recording. |
| `VIEWER` | Read-only view of aggregated dashboards and high-level scoring summaries. |

---

## 5. AI Explainer Grounding & Hallucination Prevention

To satisfy FCA compliance regarding transparent broker communications:
1. The AI explanation service ([`backend/app/services/llm_explainer.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/llm_explainer.py)) enforces a strict grounding validation pass over every generated summary.
2. **Grounding Checks**:
   - Company CRN in output must match the evaluated company CRN.
   - Every cited signal ID must exist in the input feature set.
   - Any monetary claim (e.g. `£50,000 CCJ`) must be verified against real raw signals.
3. If any claim fails validation, the LLM output is rejected and the service returns a deterministic, fact-grounded synthesis.
