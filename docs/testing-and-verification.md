# Testing, Verification & Controlled Failure Catalogue

This document provides the complete automated test catalogue, execution evidence, and failure-injection methodology verifying the robustness and accuracy of the UK Working-Capital Intelligence & Prospect Scoring Platform.

---

## 1. Test Suite Summary

- **Total Automated Tests**: 61
- **Pass Rate**: 100% (61 PASS, 0 FAIL)
- **Execution Time**: ~2.92 seconds
- **Test Framework**: `pytest 8.4.2` with `pytest-asyncio 1.2.0`
- **Python Runtime**: Python 3.9.6 on Darwin (macOS)

---

## 2. Test Execution Command & Evidence

The test suite is invoked in an isolated virtual environment:

```bash
source .venv/bin/activate
pytest -v tests/
```

### Verified Terminal Output:
```text
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
configfile: pytest.ini
collected 61 items

tests/test_api_and_review_workflow.py::test_serve_frontend_root PASSED   [  1%]
tests/test_api_and_review_workflow.py::test_system_providers PASSED      [  3%]
tests/test_api_and_review_workflow.py::test_dashboard_metrics PASSED     [  4%]
tests/test_api_and_review_workflow.py::test_prospect_table_filtering PASSED [  6%]
tests/test_api_and_review_workflow.py::test_company_profile_endpoint PASSED [  8%]
tests/test_api_and_review_workflow.py::test_broker_review_workflow PASSED [  9%]
tests/test_audit_advanced_tampering.py::test_audit_detects_field_modification_including_user_and_ip PASSED [ 11%]
tests/test_audit_advanced_tampering.py::test_audit_detects_intermediate_deletion_and_reordering PASSED [ 13%]
tests/test_audit_advanced_tampering.py::test_audit_detects_tail_truncation_via_signed_checkpoint PASSED [ 14%]
tests/test_audit_advanced_tampering.py::test_audit_detects_unauthorized_rehash_without_secret_key PASSED [ 16%]
tests/test_audit_advanced_tampering.py::test_audit_verification_endpoint_rbac PASSED [ 18%]
tests/test_audit_and_security.py::test_tamper_evident_audit_log_hash_chain PASSED [ 19%]
tests/test_audit_and_security.py::test_audit_log_tamper_detection PASSED [ 21%]
tests/test_audit_and_security.py::test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility PASSED [ 22%]
tests/test_audit_and_security.py::test_security_headers_on_responses PASSED [ 24%]
tests/test_auth_and_rbac_matrix.py::test_login_incorrect_credentials PASSED [ 26%]
tests/test_auth_and_rbac_matrix.py::test_legacy_password_verification_and_safe_upgrading PASSED [ 27%]
tests/test_auth_and_rbac_matrix.py::test_expired_jwt_token_rejected PASSED [ 29%]
tests/test_auth_and_rbac_matrix.py::test_invalid_signature_and_unsupported_algorithm_rejected PASSED [ 31%]
tests/test_auth_and_rbac_matrix.py::test_deactivated_user_token_rejected PASSED [ 32%]
tests/test_auth_and_rbac_matrix.py::test_rbac_administrative_endpoints_blocked_for_ordinary_broker PASSED [ 34%]
tests/test_companies_house_adapter.py::test_companies_house_charge_parsing_and_mr04_scope PASSED [ 36%]
tests/test_companies_house_adapter.py::test_stream_checkpoint_persistence_and_recovery PASSED [ 37%]
tests/test_companies_house_stream_resilience.py::test_companies_house_stream_416_recovery PASSED [ 39%]
tests/test_companies_house_stream_resilience.py::test_companies_house_charge_parsing_variations PASSED [ 40%]
tests/test_controlled_failures.py::test_failure_detection_invalid_crn PASSED [ 42%]
tests/test_controlled_failures.py::test_failure_detection_tampered_webhook_signature PASSED [ 44%]
tests/test_controlled_failures.py::test_failure_detection_llm_hallucination PASSED [ 45%]
tests/test_controlled_failures.py::test_failure_detection_dbt_vs_dso_confusion PASSED [ 47%]
tests/test_credit_and_workforce_adapters.py::test_creditsafe_webhook_signature_and_replay_protection PASSED [ 49%]
tests/test_credit_and_workforce_adapters.py::test_strict_dbt_vs_dso_separation PASSED [ 50%]
tests/test_credit_and_workforce_adapters.py::test_workforce_headcount_calculations PASSED [ 52%]
tests/test_crn_normalizer.py::test_normalize_standard_numeric_crn PASSED [ 54%]
tests/test_crn_normalizer.py::test_normalize_scotland_prefix PASSED      [ 55%]
tests/test_crn_normalizer.py::test_normalize_northern_ireland_and_llp PASSED [ 57%]
tests/test_crn_normalizer.py::test_invalid_crn_handling PASSED           [ 59%]
tests/test_crn_normalizer.py::test_is_valid_uk_crn_convenience PASSED    [ 60%]
tests/test_database_init.py::test_database_initialization PASSED         [ 62%]
tests/test_database_init.py::test_password_hashing PASSED                [ 63%]
tests/test_dead_letter_replay.py::test_dead_letter_replay_lifecycle PASSED [ 65%]
tests/test_deployment_and_clean_start.py::test_missing_credentials_does_not_silently_promote_fixtures_when_demo_mode_false PASSED [ 67%]
tests/test_deployment_and_clean_start.py::test_clean_health_and_system_providers_endpoint PASSED [ 68%]
tests/test_entity_resolution.py::test_exact_crn_resolution PASSED        [ 70%]
tests/test_entity_resolution.py::test_high_confidence_deterministic_match PASSED [ 72%]
tests/test_entity_resolution.py::test_ambiguous_match_routes_to_review_queue PASSED [ 73%]
tests/test_entity_resolution_conflicts.py::test_conflicting_crn_prevents_false_positive_merge PASSED [ 75%]
tests/test_entity_resolution_conflicts.py::test_entity_unmerge_and_split_reversal PASSED [ 77%]
tests/test_feature_store_point_in_time.py::test_point_in_time_historical_isolation PASSED [ 78%]
tests/test_llm_explainer.py::test_deterministic_explanation_generation_and_contract PASSED [ 80%]
tests/test_llm_explainer.py::test_grounding_validation_rejects_hallucinated_signal_ids PASSED [ 81%]
tests/test_point_in_time_and_corroboration_matrix.py::test_withdrawn_and_disputed_signals_strictly_excluded PASSED [ 83%]
tests/test_point_in_time_and_corroboration_matrix.py::test_same_underlying_event_reported_by_two_providers_does_not_corroborate PASSED [ 85%]
tests/test_point_in_time_and_corroboration_matrix.py::test_genuine_multi_source_corroboration_exceeds_cap PASSED [ 86%]
tests/test_point_in_time_leakage_remediation.py::test_late_arriving_event_leakage_prevention PASSED [ 88%]
tests/test_point_in_time_leakage_remediation.py::test_disputed_and_superseded_signals_excluded PASSED [ 90%]
tests/test_scoring_engine.py::test_single_source_uncorroborated_score_capped PASSED [ 91%]
tests/test_scoring_engine.py::test_multi_source_corroborated_high_strain_score PASSED [ 93%]
tests/test_scoring_engine.py::test_contradictory_evidence_mitigation PASSED [ 95%]
tests/test_stream_resilience_and_continuity.py::test_stream_416_records_explicit_degraded_gap PASSED [ 96%]
tests/test_stream_resilience_and_continuity.py::test_checkpoint_cannot_advance_beyond_durably_handled_events PASSED [ 98%]
tests/test_stream_resilience_and_continuity.py::test_stream_reconnection_deduplicates_events PASSED [100%]

======================== 61 passed in 2.92s =========================
```

---

## 3. Test Catalogue by Subsystem

### A. Audit Trail & Cryptographic Tamper Evidence
- [`tests/test_audit_advanced_tampering.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_advanced_tampering.py):
  1. `test_audit_detects_field_modification_including_user_and_ip`: Verifies modifications to `user_email`, `user_id`, or `ip_address` break verification.
  2. `test_audit_detects_intermediate_deletion_and_reordering`: Verifies intermediate deletion breaks hash linking.
  3. `test_audit_detects_tail_truncation_via_signed_checkpoint`: Verifies tail truncation fails signed checkpoint comparison.
  4. `test_audit_detects_unauthorized_rehash_without_secret_key`: Verifies mass re-hashing fails HMAC signature verification.
  5. `test_audit_verification_endpoint_rbac`: Verifies 401 unauthenticated, 403 broker, 200 admin/analyst.
- [`tests/test_audit_and_security.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py):
  6. `test_tamper_evident_audit_log_hash_chain`: Sequential hash chaining verification.
  7. `test_audit_log_tamper_detection`: Detail field tampering verification.
  8. `test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility`: 210,000 PBKDF2 iterations verification.
  9. `test_security_headers_on_responses`: CSP, HSTS, X-Frame-Options, and X-Content-Type-Options verification.

### B. Streaming Ingestion Resilience & Checkpoints
- [`tests/test_stream_resilience_and_continuity.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_stream_resilience_and_continuity.py):
  1. `test_stream_416_records_explicit_degraded_gap`: Verifies HTTP 416 sets `degraded_gap` status with timepoint boundaries.
  2. `test_checkpoint_cannot_advance_beyond_durably_handled_events`: Verifies cursor stops at the last committed event.
  3. `test_stream_reconnection_deduplicates_events`: Verifies duplicate events on reconnection are deduplicated via payload hash.
- [`tests/test_companies_house_stream_resilience.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_companies_house_stream_resilience.py):
  4. `test_companies_house_stream_416_recovery`: Verifies 416 error handling and cursor reset.
  5. `test_companies_house_charge_parsing_variations`: Parses full vs partial scope charge satisfactions.

### C. Authentication & Role-Based Access Control
- [`tests/test_auth_and_rbac_matrix.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_auth_and_rbac_matrix.py):
  1. `test_login_incorrect_credentials`: HTTP 401 on bad credentials.
  2. `test_legacy_password_verification_and_safe_upgrading`: Legacy 100k password upgrades to 210,000 iterations on login.
  3. `test_expired_jwt_token_rejected`: HTTP 401 on expired tokens.
  4. `test_invalid_signature_and_unsupported_algorithm_rejected`: Rejects forged signatures and `alg: "none"`.
  5. `test_deactivated_user_token_rejected`: Rejects tokens belonging to deactivated users.
  6. `test_rbac_administrative_endpoints_blocked_for_ordinary_broker`: 403 on DLQ replay, list DLQ, demo seed, audit verification, and unmerge.

### D. Point-in-Time Correctness & Corroboration Engine
- [`tests/test_point_in_time_and_corroboration_matrix.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_point_in_time_and_corroboration_matrix.py):
  1. `test_withdrawn_and_disputed_signals_strictly_excluded`: Ensures withdrawn and disputed records are omitted.
  2. `test_same_underlying_event_reported_by_two_providers_does_not_corroborate`: Deduplicates identical underlying CCJs across providers; score strictly capped at 49.0.
  3. `test_genuine_multi_source_corroboration_exceeds_cap`: Genuinely independent multi-provider signals exceed 49.0 into elevated/strong bands.
- [`tests/test_point_in_time_leakage_remediation.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_point_in_time_leakage_remediation.py):
  4. `test_late_arriving_event_leakage_prevention`: Enforces `ingestion_timestamp <= as_of` preventing lookahead leakage.
  5. `test_disputed_and_superseded_signals_excluded`: Ensures superseded filings are excluded.
- [`tests/test_scoring_engine.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_scoring_engine.py):
  6. `test_single_source_uncorroborated_score_capped`: Single source uncorroborated score capped at 49.0.
  7. `test_multi_source_corroborated_high_strain_score`: Multi-source corroborated scoring.
  8. `test_contradictory_evidence_mitigation`: Dampens strain when positive indicators exist.

### E. Deployment Readiness & Credential Truthfulness
- [`tests/test_deployment_and_clean_start.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_deployment_and_clean_start.py):
  1. `test_missing_credentials_does_not_silently_promote_fixtures_when_demo_mode_false`: Truthfully returns UNCONFIGURED and empty lists when `DEMO_MODE=False`.
  2. `test_clean_health_and_system_providers_endpoint`: Verifies `/system/health` and `/system/providers`.
