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

### B. Tamper-Evident Audit Log Hash Chaining & Signed Checkpoints
- **Model**: [`AuditLog`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/user.py) and [`AuditCheckpoint`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/user.py)
- **Service**: [`backend/app/services/audit.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/audit.py)
- Every audit entry (user login, prospect status transition, manual note creation) computes a cryptographic hash binding all 10 canonical fields:
  $$\text{record\_hash} = \text{SHA-256}(\text{prev\_hash} \,\|\, \text{user\_id} \,\|\, \text{user\_email} \,\|\, \text{user\_role} \,\|\, \text{action} \,\|\, \text{resource\_type} \,\|\, \text{resource\_id} \,\|\, \text{details} \,\|\, \text{ip\_address} \,\|\, \text{timestamp})$$
- The genesis entry uses `GENESIS_HASH = "GENESIS_BLOCK_HASH_UK_WORKING_CAPITAL_2026"`.
- **Independently Signed Checkpoints**:
  - Periodically and upon commits, an `AuditCheckpoint` record is stored using a domain-separated key (`AUDIT_CHECKPOINT_SECRET` or `HMAC-SHA256(SECRET_KEY, "uk-wci-audit-checkpoint-signing-key-v2")`):
    $$\text{signature} = \text{HMAC-SHA256}(K_{\text{checkpoint}}, \, \text{sequence} \,\|\, \text{last\_audit\_id} \,\|\, \text{last\_record\_hash} \,\|\, \text{total\_records})$$
  - Verification (`verify_audit_log_chain(db, verify_checkpoints=True)`) rejects any record with missing/empty `prev_hash` or `record_hash`, verifies monotonic IDs and timestamps, and verifies **every** checkpoint in sequence against its referenced `AuditLog` record.
  - If an attacker truncates tail records or modifies historical rows and recomputes the `AuditLog` hash chain without possessing $K_{\text{checkpoint}}$, checkpoint verification fails immediately.

---

## 6. Outbound SSRF Boundary, PECR/UK GDPR Enforcement & Route Authorisation

### A. Central Outbound SSRF Protection (`backend/app/core/ssrf_protection.py`)
- All external HTTP requests (`MarketingChannelDiscoveryService`, `AccountsFetcher`, `ContractsFinderAdapter`, `GazetteAdapter`, `ComplianceRiskAgent`) pass through `validate_safe_url` and `SafeHttpClient`.
- Rejects cleartext HTTP by default, embedded URL credentials, non-standard ports, IPv4/IPv6 loopback (`127.0.0.0/8`, `::1`), RFC1918 private ranges, link-local/cloud metadata (`169.254.169.254`, `fe80::/10`), carrier-grade NAT (`100.64.0.0/10`), IPv4-mapped IPv6 (`::ffff:127.0.0.1`), 6to4, Teredo, and numeric/hex/octal IP obfuscations.
- Fails closed on DNS resolution errors (`socket.gaierror`), disables environment proxy inheritance (`trust_env=False`), revalidates every redirect hop (max 3 hops), and enforces dual pre-connection DNS checks against DNS rebinding.

### B. PECR Regulations 22 & 23 and UK GDPR Article 21 Enforcement (`backend/app/services/identity_resolution.py`)
- Fails closed (`UNKNOWN_SUBSCRIBER_STATUS_BLOCKED`) whenever `company_type` is missing, empty, or unrecognised—never defaulting to `"ltd"`.
- Distinguishes UK corporate subscribers (`ltd`, `plc`, `llp`, `scottish-partnership`, `royal-charter`) from individual subscribers (`sole-trader`, English/Welsh general `partnership` without prior opt-in consent under PECR Reg 22) and non-UK entities (`OUT_OF_SCOPE_NON_UK`).
- Enforces PECR Regulation 23 across all electronic marketing communications (mandatory sender identity disclosure and valid opt-out address/mechanism).
- Enforces persistent suppression across CRN, person ID, normalized name, email address, and email domain at discovery, strategy generation, campaign tracking, and audience activation execution time.
- **Integrity Verification & RBAC**:
  - `GET /api/v1/system/audit-verification` verifies both the sequential chain and the HMAC checkpoint signature.
  - Restricted strictly to `ADMIN` and `ANALYST` roles.
- **Threat Model & Trust Assumptions**:
  - **Not Physically Immutable**: The log is tamper-evident. If an adversary compromises both the database storage volume and the application host's `SECRET_KEY`, they could forge new checkpoints.
  - For full enterprise non-repudiation, checkpoint digests should be mirrored out-of-band to WORM object storage (e.g. AWS S3 Object Lock or Azure Immutable Blob).

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
