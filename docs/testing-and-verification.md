# Testing, Verification & Controlled Failure Catalogue

This document provides the complete automated test catalogue, execution evidence, and failure-injection methodology verifying the robustness and accuracy of the UK Working-Capital Intelligence & Prospect Scoring Platform.

---

## 1. Test Suite Summary

- **Total Automated Tests**: 42
- **Pass Rate**: 100% (42 PASS, 0 FAIL)
- **Execution Time**: ~2.60 seconds
- **Test Framework**: `pytest 8.4.2` with `pytest-asyncio 1.2.0`
- **Python Runtime**: Python 3.9.6 on Darwin (macOS)

---

## 2. Test Execution Command & Evidence

The test suite is invoked in an isolated virtual environment:

```bash
source .venv/bin/activate
PYTHONPATH=. pytest -v tests/
```

### Verified Terminal Output:
```text
tests/test_api_and_review_workflow.py::test_serve_frontend_root PASSED   [  2%]
tests/test_api_and_review_workflow.py::test_system_providers PASSED      [  4%]
tests/test_api_and_review_workflow.py::test_dashboard_metrics PASSED     [  7%]
tests/test_api_and_review_workflow.py::test_prospect_table_filtering PASSED [  9%]
tests/test_api_and_review_workflow.py::test_company_profile_endpoint PASSED [ 11%]
tests/test_api_and_review_workflow.py::test_broker_review_workflow PASSED [ 14%]
tests/test_audit_and_security.py::test_tamper_evident_audit_log_hash_chain PASSED [ 16%]
tests/test_audit_and_security.py::test_audit_log_tamper_detection PASSED [ 19%]
tests/test_audit_and_security.py::test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility PASSED [ 21%]
tests/test_audit_and_security.py::test_security_headers_on_responses PASSED [ 23%]
tests/test_companies_house_adapter.py::test_companies_house_charge_parsing_and_mr04_scope PASSED [ 26%]
tests/test_companies_house_adapter.py::test_stream_checkpoint_persistence_and_recovery PASSED [ 28%]
tests/test_companies_house_stream_resilience.py::test_companies_house_stream_416_recovery PASSED [ 30%]
tests/test_companies_house_stream_resilience.py::test_companies_house_charge_parsing_variations PASSED [ 33%]
tests/test_controlled_failures.py::test_failure_detection_invalid_crn PASSED [ 35%]
tests/test_controlled_failures.py::test_failure_detection_tampered_webhook_signature PASSED [ 38%]
tests/test_controlled_failures.py::test_failure_detection_llm_hallucination PASSED [ 40%]
tests/test_controlled_failures.py::test_failure_detection_dbt_vs_dso_confusion PASSED [ 42%]
tests/test_credit_and_workforce_adapters.py::test_creditsafe_webhook_signature_and_replay_protection PASSED [ 45%]
tests/test_credit_and_workforce_adapters.py::test_strict_dbt_vs_dso_separation PASSED [ 47%]
tests/test_workforce_headcount_calculations PASSED [ 50%]
tests/test_crn_normalizer.py::test_normalize_standard_numeric_crn PASSED [ 52%]
tests/test_crn_normalizer.py::test_normalize_scotland_prefix PASSED      [ 54%]
tests/test_crn_normalizer.py::test_normalize_northern_ireland_and_llp PASSED [ 57%]
tests/test_crn_normalizer.py::test_invalid_crn_handling PASSED           [ 59%]
tests/test_crn_normalizer.py::test_is_valid_uk_crn_convenience PASSED    [ 61%]
tests/test_database_init.py::test_database_initialization PASSED         [ 64%]
tests/test_database_init.py::test_password_hashing PASSED                [ 66%]
tests/test_dead_letter_replay.py::test_dead_letter_replay_lifecycle PASSED [ 69%]
tests/test_entity_resolution.py::test_exact_crn_resolution PASSED        [ 71%]
tests/test_entity_resolution.py::test_high_confidence_deterministic_match PASSED [ 73%]
tests/test_entity_resolution.py::test_ambiguous_match_routes_to_review_queue PASSED [ 76%]
tests/test_entity_resolution_conflicts.py::test_conflicting_crn_prevents_false_positive_merge PASSED [ 78%]
tests/test_entity_resolution_conflicts.py::test_entity_unmerge_and_split_reversal PASSED [ 80%]
tests/test_feature_store_point_in_time.py::test_point_in_time_historical_isolation PASSED [ 83%]
tests/test_llm_explainer.py::test_deterministic_explanation_generation_and_contract PASSED [ 85%]
tests/test_llm_explainer.py::test_grounding_validation_rejects_hallucinated_signal_ids PASSED [ 88%]
tests/test_point_in_time_leakage_remediation.py::test_late_arriving_event_leakage_prevention PASSED [ 90%]
tests/test_point_in_time_leakage_remediation.py::test_disputed_and_superseded_signals_excluded PASSED [ 92%]
tests/test_scoring_engine.py::test_single_source_uncorroborated_score_capped PASSED [ 95%]
tests/test_scoring_engine.py::test_multi_source_corroborated_high_strain_score PASSED [ 97%]
tests/test_scoring_engine.py::test_contradictory_evidence_mitigation PASSED [100%]

============================== 42 passed in 2.60s ==============================
```

---

## 3. Test Catalogue by Subsystem

### A. Controlled Failure & Adversarial Verification
- [`tests/test_controlled_failures.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_controlled_failures.py):
  1. `test_failure_detection_invalid_crn`: Verifies invalid formats (short, punctuation, illicit prefixes) fail validation.
  2. `test_failure_detection_tampered_webhook_signature`: Injects bit-flip into HMAC signature and verifies HTTP 401 rejection.
  3. `test_failure_detection_llm_hallucination`: Injects fake signal IDs into LLM output and verifies rejection.
  4. `test_failure_detection_dbt_vs_dso_confusion`: Verifies schema rejection if DBT metric is passed into DSO field.

### B. Security, Cryptography & Audit Ledger
- [`tests/test_audit_and_security.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_audit_and_security.py):
  1. `test_tamper_evident_audit_log_hash_chain`: Verifies SHA-256 hash chaining across multiple operations.
  2. `test_audit_log_tamper_detection`: Modifies database record directly and proves verification flags corruption.
  3. `test_pbkdf2_210k_iteration_hashing_and_legacy_compatibility`: Verifies 210,000 PBKDF2 rounds and legacy migration.
  4. `test_security_headers_on_responses`: Verifies CSP, HSTS, X-Frame-Options, and X-Content-Type-Options.

### C. Point-in-Time Correctness & Bi-Temporal Integrity
- [`tests/test_point_in_time_leakage_remediation.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_point_in_time_leakage_remediation.py):
  1. `test_late_arriving_event_leakage_prevention`: Enforces `ingestion_timestamp <= as_of` preventing lookahead bias.
  2. `test_disputed_and_superseded_signals_excluded`: Ensures disputed filings are immediately omitted from features.
- [`tests/test_feature_store_point_in_time.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_feature_store_point_in_time.py):
  3. `test_point_in_time_historical_isolation`: Validates historical backtest repeatability.

### D. Entity Resolution & Conflict Remediation
- [`tests/test_entity_resolution_conflicts.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_entity_resolution_conflicts.py):
  1. `test_conflicting_crn_prevents_false_positive_merge`: Proves matching names/postcodes with distinct CRNs are NOT merged.
  2. `test_entity_unmerge_and_split_reversal`: Tests analyst-driven entity separation and re-assignment.
- [`tests/test_entity_resolution.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_entity_resolution.py):
  3. `test_exact_crn_resolution`: Validates 100% deterministic link on identical CRN.
  4. `test_high_confidence_deterministic_match`: Validates name/postcode heuristic matching.
  5. `test_ambiguous_match_routes_to_review_queue`: Validates low-confidence matching is queued for human review.

### E. Ingestion Resilience & Dead Letter Replay
- [`tests/test_dead_letter_replay.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_dead_letter_replay.py):
  1. `test_dead_letter_replay_lifecycle`: Ingests malformed payload, confirms DLQ storage, repairs schema, and successfully replays.
- [`tests/test_companies_house_stream_resilience.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_companies_house_stream_resilience.py):
  2. `test_companies_house_stream_416_recovery`: Verifies recovery when timepoint expires.
  3. `test_companies_house_charge_parsing_variations`: Parses full vs partial charge satisfactions.

### F. Scoring Engine v1.0.0 & Explainability
- [`tests/test_scoring_engine.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_scoring_engine.py):
  1. `test_single_source_uncorroborated_score_capped`: Verifies score is hard-capped at 49.0 without 2nd source.
  2. `test_multi_source_corroborated_high_strain_score`: Proves multi-source signals achieve High/Critical bands (>75.0).
  3. `test_contradictory_evidence_mitigation`: Proves strong credit/headcount metrics deduct strain penalty.
- [`tests/test_llm_explainer.py`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_llm_explainer.py):
  4. `test_deterministic_explanation_generation_and_contract`: Verifies structured explanation schema.
  5. `test_grounding_validation_rejects_hallucinated_signal_ids`: Validates hallucination rejection.
