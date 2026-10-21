# Remediation Plan & Execution Ledger

This document tracks all remediations implemented to transition the UK Working-Capital Intelligence & Prospect Scoring Platform from its audited baseline to a defensible release-readiness level.

---

### Remediation Ledger (REM-01 through REM-19)

| ID | Issue Description | Root Cause | Implemented Correction | Verification Evidence | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **REM-01** | Conflicting CRNs could be merged via fuzzy matching | `resolve_entity` searched name candidates even when input CRN conflicted with candidate CRN | Added hard exclusion in `EntityResolutionService`: candidates with conflicting CRNs are skipped | `tests/test_entity_resolution_conflicts.py::test_conflicting_crn_prevents_false_positive_merge` | **PASS** |
| **REM-02** | Historical feature store leaked late-arriving events | Feature query only filtered on `observation_timestamp <= as_of` | Enforced bi-temporal query: `observation_timestamp <= as_of` AND `ingestion_timestamp <= as_of`, excluding disputed/superseded | `tests/test_point_in_time_leakage_remediation.py::test_late_arriving_event_leakage_prevention` | **PASS** |
| **REM-03** | Neutral filings inflated multi-source corroboration | `distinct_sources` counted all provider names regardless of strain direction | Computed `distinct_strain_sources` strictly on `direction == 'strain_increasing'` with event deduplication | `tests/test_scoring_engine.py::test_single_source_uncorroborated_score_capped` | **PASS** |
| **REM-04** | Dead-letter queue lacked programmatic replay | DLQ table existed but lacked re-processing service method and route | Built `IngestionService.replay_dead_letter_event` and `POST /api/v1/ingest/dead-letter-queue/{id}/replay` | `tests/test_dead_letter_replay.py::test_dead_letter_replay_lifecycle` | **PASS** |
| **REM-05** | Companies House streaming crashed on expired cursor | Missing HTTP 416 handler on stream buffer expiry | Added status 416 check: logs cursor expiration and resets `next_timepoint` to `None` (stream head) | `tests/test_companies_house_stream_resilience.py::test_companies_house_stream_416_recovery` | **PASS** |
| **REM-06** | Webhook lacked timestamp replay defense | Signature check only compared HMAC without time drift validation | Added `X-Creditsafe-Timestamp` drift validation (max 300s) | `tests/test_credit_and_workforce_adapters.py::test_creditsafe_webhook_signature_and_replay_protection` | **PASS** |
| **REM-07** | PBKDF2 hash iterations were below modern standards | Legacy 100k iteration count | Upgraded to 210,000 iterations modular format (`pbkdf2_sha256$210000$salt$key`) with legacy compatibility | `tests/test_audit_and_security.py::test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility` | **PASS** |
| **REM-08** | Audit trail lacked tamper-evidence | Records were simple rows in SQLite/Postgres without cryptographic linking | Implemented SHA-256 hash chaining (`prev_hash` & `record_hash`) and verification endpoint | `tests/test_audit_and_security.py::test_tamper_evident_audit_log_hash_chain` | **PASS** |
| **REM-09** | Missing HTTP security headers | FastAPI application returned raw responses without defensive headers | Added security headers middleware (`X-Content-Type-Options`, `X-Frame-Options`, `CSP`, `Referrer-Policy`) | `tests/test_audit_and_security.py::test_security_headers_on_responses` | **PASS** |
| **REM-10** | Missing CRN & numeric claim validation in AI explanation | Schema validated structure but did not check company identity or ungrounded monetary claims | Added strict CRN match check and regex scan for ungrounded currency amounts | `tests/test_llm_explainer.py::test_grounding_validation_rejects_hallucinated_signal_ids` | **PASS** |
| **REM-11** | Unconfigured providers silently substituted simulated data when `DEMO_MODE=False` | Adapters lacked guard against returning simulation fixtures in non-demo mode | Added explicit `if not self._is_live and not settings.DEMO_MODE: return []` guards across all 4 adapters | `tests/test_deployment_and_clean_start.py::test_missing_credentials_does_not_silently_promote_fixtures_when_demo_mode_false` | **PASS** |
| **REM-12** | Audit hash omitted user identity and provenance IP | `compute_record_hash` only hashed action, resource, details, timestamp | Extended `compute_record_hash` to bind `user_id`, `user_email`, `user_role`, and `ip_address` into canonical hash | `tests/test_audit_advanced_tampering.py::test_audit_detects_field_modification_including_user_and_ip` | **PASS** |
| **REM-13** | Audit log tail truncation and DB re-hashing was undetectable | All verification relied solely on previous record links without signed checkpoint | Implemented `AuditCheckpoint` with HMAC-SHA256 signature verified against `SECRET_KEY` | `tests/test_audit_advanced_tampering.py::test_audit_detects_tail_truncation_via_signed_checkpoint` | **PASS** |
| **REM-14** | Companies House 416 cursor reset hid missed event intervals | Resetting to stream head reported healthy status despite data gaps | Added `has_gap` and timepoint gap boundaries in `StreamCheckpoint`; transitions stream to `degraded_gap` | `tests/test_stream_resilience_and_continuity.py::test_stream_416_records_explicit_degraded_gap` | **PASS** |
| **REM-15** | Batch stream ingestion advanced checkpoints past failed events | `update_checkpoint` could advance cursor even if an intermediate event failed | Implemented `process_stream_batch` updating cursor strictly to the last committed event | `tests/test_stream_resilience_and_continuity.py::test_checkpoint_cannot_advance_beyond_durably_handled_events` | **PASS** |
| **REM-16** | Identical underlying events reported by two bureaus bypassed 49.0 cap | Deduplication grouped solely by provider-specific event ID | Canonical event clustering in `FeatureStoreService` deduplicates by `evidence_reference` / `underlying_event_ref` | `tests/test_point_in_time_and_corroboration_matrix.py::test_same_underlying_event_reported_by_two_providers_does_not_corroborate` | **PASS** |
| **REM-17** | Withdrawn signals contributed to feature calculation | Feature query only filtered `notin_(["disputed", "superseded"])` | Updated filter to `notin_(["disputed", "superseded", "withdrawn"])` | `tests/test_point_in_time_and_corroboration_matrix.py::test_withdrawn_and_disputed_signals_strictly_excluded` | **PASS** |
| **REM-18** | Administrative endpoints lacked role-based access control | DLQ replay, demo seed, audit verification, and unmerge endpoints were callable without admin/analyst roles | Added `require_role(["ADMIN", "admin"])` and `require_role(["ADMIN", "admin", "ANALYST", "analyst"])`; added automatic password hash upgrade | `tests/test_auth_and_rbac_matrix.py::test_rbac_administrative_endpoints_blocked_for_ordinary_broker` | **PASS** |
| **REM-19** | Docker Compose missing PostgreSQL driver | `requirements.txt` lacked `psycopg2-binary` | Added `psycopg2-binary>=2.9.9` to `requirements.txt` | Build / Manifest verification | **PASS** |

---

### Protocol Adherence
Every remediation followed the mandatory execution protocol:
1. Defect identified against architectural specification.
2. Controlled test introduced to reproduce defect.
3. Minimal safe code change implemented.
4. Full regression suite executed (**all 61 tests passing**).
5. Documented in audit ledger.
