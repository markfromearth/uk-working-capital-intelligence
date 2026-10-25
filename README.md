# UK Working-Capital Intelligence & Prospect Scoring Platform

A secure, observable, production-grade B2B intelligence platform designed for commercial finance brokers, business development managers, and prospecting analysts to identify UK businesses potentially approaching a working-capital funding requirement.

---

## 1. Architectural Highlights

- **Canonical UK Entity Identity**: Strict UK Company Registration Number (CRN) normalization (8-character alphanumeric, Scottish `SC`, Northern Irish `NI`, and LLP `OC` support).
- **Core Signal Ingestion Adapters**:
  - **Companies House**: Polling & Charges Streaming adapter capturing Form MR04 (Statement of Satisfaction of a Charge) and registrations.
  - **Creditsafe Connect**: Commercial credit risk adapter monitoring Days Beyond Terms (DBT) and score downgrades with HMAC webhook signature verification.
  - **Experian Commercial**: Commercial risk adapter maintaining strict segregation between DBT and DSO.
  - **Workforce Intelligence**: Aggregate employee headcount dynamics tracking 30d/90d/180d growth rates, hiring acceleration, and observation spacing confidence.
- **Strict DBT vs DSO Separation**: Days Beyond Terms (supplier payment lateness) is strictly separated from Days Sales Outstanding (customer collection duration). DSO is marked unobserved rather than fabricated.
- **Liquidity Strain Score (v1.0.0)**: Transparent 0–100 score across 4 pillars:
  1. Corporate financing-position changes (max 25 pts)
  2. Commercial credit and payment-risk deterioration (max 35 pts)
  3. Workforce expansion & payroll burn pressure (max 20 pts)
  4. Cross-signal corroboration & trend persistence (max 20 pts)
- **Evidence-Grounded AI Explanations**: Bounded schema-constrained structured output contract referencing verified stored signal IDs. Prompt injection hardened with zero-hallucination deterministic fallbacks.
- **Autonomous Multi-Agent Architecture (Agents A through J)**: Decoupled, typed asynchronous agents communicating through versioned Pydantic envelopes with budget tracking and an independent verification gate:
  - **Agent A (`SIGNAL_DETECTION`)**: Ingests statutory Companies House filings, OCDS contract awards from Contracts Finder, and insolvency petitions from The Gazette.
  - **Agent B (`ENTITY_RESOLUTION`)**: Canonical entity matching and cross-source record unmerging.
  - **Agent C (`ADAPTIVE_INVESTIGATION`)**: Free-first Value-of-Information (EVOI) search over official public databases.
  - **Agent D (`FINANCE_OPPORTUNITY`)**: Quant accounts extraction, FRS 102/105 balance sheet reconciliation, and 80% borrowing base facility sizing.
  - **Agent E (`MARKETING_RESEARCH`)**: B2B marketing channel discovery across UK podcasts, trade associations, and industry conferences.
  - **Agent F (`AUDIENCE_STRATEGY`)**: Formulates differentiated commercial campaign briefs, creative angles, and low-cost validation test plans.
  - **Agent G (`CAMPAIGN_ECONOMICS`)**: Full-funnel commercial unit economics modeling across Conservative, Base, and Optimistic scenarios.
  - **Agent H (`CAMPAIGN_MEASUREMENT`)**: Issues deterministic tracking IDs (`CMP-YYYYMM-XXXX`), ingests outcomes via CSV, and updates Bayesian EVOI priors.
  - **Agent I (`COMPLIANCE_RISK`)**: Enforces PECR Regulation 23 Corporate Subscriber rules, suppression checks, and SSRF defenses.
  - **Agent J (`INDEPENDENT_VERIFICATION`)**: Cryptographic audit inspection verifying that zero ungrounded or fabricated claims are emitted.
- **Restrained B2B Fintech UI**: Responsive data-dense dashboard, prioritized prospect table, multi-parameter filtering, interactive company profiles, trapped cash dossiers, and unified marketing intelligence workspace.

---

## 2. Quickstart & Local Setup

### Prerequisites
- Python 3.9+
- SQLite (included) or PostgreSQL 15+

### Installation
```bash
# Clone repository and enter directory
cd eager-planck

# Create virtual environment and activate
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Application
```bash
# Start backend server (serves both API and the frontend dashboard at http://localhost:8000)
source .venv/bin/activate
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Visit **`http://localhost:8000`** in your browser to access the interactive broker intelligence dashboard.
API documentation is available at **`http://localhost:8000/docs`**.

---

## 3. Running Automated Tests

The platform includes **164 comprehensive automated tests** (100% passing) spanning unit, integration, entity resolution, point-in-time correctness, multi-agent orchestration, marketing intelligence, and controlled failure-detection tests:

```bash
source .venv/bin/activate
pytest -v
```

### Complete Verification Suite (164 Tests)
1. `tests/test_procurement_and_gazette.py`: Contracts Finder OCDS awards, The Gazette statutory notices, and provisional score knockout logic.
2. `tests/test_agent_orchestration.py`: End-to-end 10-agent orchestration (Agents A through J), budget enforcement, and rejection of ungrounded claims.
3. `tests/test_marketing_discovery.py`: Free B2B channel discovery, iTunes Podcast search, trade association mapping, and strictly unobserved audience metrics.
4. `tests/test_campaign_strategy.py`: Differentiated B2B campaign briefs, creative angles, PECR guardrails, and validation test plans.
5. `tests/test_campaign_economics.py`: Full-funnel unit economics modeling across Conservative, Base, and Optimistic bounds, break-even response rates, and assumption isolation.
6. `tests/test_campaign_learning.py`: Deterministic tracking codes (`CMP-YYYYMM-XXXX`), CSV batch ingestion, point-in-time zero leakage, and Bayesian EVOI updates.
7. `tests/test_marketing_api.py`: Marketing REST API endpoints and dashboard UI integration.
8. `tests/test_marketing_compliance_adversarial.py`: PECR Corporate Subscriber verification (Reg 22 vs Reg 23), SSRF protections, and cryptographic audit tampering detection.
9. `tests/test_crn_normalizer.py`: Numeric padding, Scottish `SC`, Northern Irish `NI`, LLP `OC`, and error handling.
10. `tests/test_companies_house_adapter.py`: MR04 charge parsing, full vs partial scope detection, stream checkpoint persistence, and resume cursor recovery.
11. `tests/test_credit_and_workforce_adapters.py`: Creditsafe webhook HMAC verification, replay protection, strict DBT vs DSO separation, and headcount growth/acceleration calculations.
12. `tests/test_entity_resolution.py`: Exact CRN matching, high-confidence fuzzy matching with postcode boost, and manual review routing.
13. `tests/test_feature_store_point_in_time.py`: Historical point-in-time isolation proving no future signal leakage.
14. `tests/test_scoring_engine.py`: Multi-source corroboration, single-source caps, and contradictory evidence dampening.
15. `tests/test_audit_and_security.py` & `test_audit_advanced_tampering.py`: Cryptographic hash chain verification and tampering detection.
16. `tests/test_adaptive_investigation_and_ml.py`: EVOI evaluation, tool registry, and zero-cost public tool preference.
15. `tests/test_ml_training_and_evaluation.py`: Walk-forward validation, disjoint company clustering, candidate model training, rules baseline head-to-head metrics, refusal guards, and model card serialization.
16. `tests/test_ml_shadow_logging_and_promotion.py`: Shadow prediction persistence in `model_predictions`, cluster bootstrap confidence interval evaluation, `/ml/status`, and manual admin promotion workflow.
17. `tests/test_ml_evoi_learning.py`: Canonical missingness pattern extraction, prior fallbacks, Bayesian-smoothed empirical EVOI learning, and investigation run outcome logging.

---

## 4. Machine Learning Production Promotion Layer

The platform incorporates a production-promotable, honest machine learning layer operating alongside the deterministic rules engine:

- **Single Feature Specification (`backend/app/ml/features.py`)**: 15 canonical features (10 continuous, 5 explicit missingness indicators) with monotonic tree constraints and SHA-256 version hash.
- **Point-in-Time Dataset Construction (`backend/app/ml/dataset.py`)**: Strictly 1 row per `(crn, snapshot_date)`, future-leakage prevention via bi-temporal query bounds, and right-censoring exclusion.
- **Dual Label Semantics**:
  - `new_charge_12m`: Statutory proxy measuring broader UK SME **commercial finance DEMAND** from Companies House filings.
  - `broker_funded`: Verified **broker sales funnel conversion**.
- **Candidate Models & Honest Evaluation (`backend/app/ml/train.py`)**:
  - Regularized Logistic Regression (median imputer + standard scaler + L2 penalty).
  - Monotonic HistGradientBoosting.
  - Calibrated HistGradientBoosting (calibrated on a held-out temporal validation slice via `FrozenEstimator`).
  - Expanding-window temporal walk-forward splits combined with disjoint company GroupKFold (no company appears in both train and test splits).
  - Out-of-sample metrics (ROC-AUC, PR-AUC, Brier score, Top-K Precision / Lift for $k=10, 25, 50$) evaluated head-to-head against the rules baseline on identical out-of-sample test rows.
  - Training refusal guard requiring $\ge 200$ companies and $\ge 30$ positives to prevent fitting toy models on unrepresentative data.
- **Shadow Mode & Statistical Promotion Gate (`backend/app/ml/promotion.py`)**:
  - Predictions logged per assessment in `model_predictions` table.
  - Cluster bootstrap confidence intervals (resampled by company, 1,000 iterations). Candidate models are eligible for promotion only if 95% bootstrap CI shows statistically significant improvement in Top-10 Precision and Brier score over the rules engine.
  - Manual Admin promotion endpoint (`POST /api/v1/adaptive/ml/promote`) with immutable audit logging.
- **Empirical EVOI Learning (`backend/app/ml/evoi_learning.py`)**:
  - `investigation_run_logs` tracks empirical prior/posterior actions and tiers upon tool execution.
  - Empirical $P(\text{action change} \mid \text{tool}, \text{missingness pattern})$ estimated using Bayesian smoothing ($N_0 = 10$).
  - Safe fallback to heuristic domain priors when sample count $< 20$.

---

## 5. Configuration & Environment Variables

Copy `.env.example` to `.env` to configure optional live provider credentials:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `DATABASE_URL` | SQLAlchemy connection string | `sqlite:///./working_capital.db` |
| `COMPANIES_HOUSE_API_KEY` | Official Companies House API key | *(Empty - enables simulation fixture)* |
| `CREDITSAFE_API_KEY` | Creditsafe Connect subscription key | *(Empty - enables simulation fixture)* |
| `CREDITSAFE_WEBHOOK_SECRET` | HMAC SHA-256 secret for webhooks | *(Empty)* |
| `EXPERIAN_API_KEY` | Experian Commercial risk API key | *(Empty - enables simulation fixture)* |
| `PROXYCURL_API_KEY` | Proxycurl / Workforce API key | *(Empty - enables simulation fixture)* |
| `ANTHROPIC_API_KEY` | Claude API key for LLM explanations | *(Empty - enables deterministic fallback)* |
| `DEMO_MODE` | Populate realistic synthetic fixtures | `true` |

---

## 5. Operational Runbooks

### Streaming Checkpoint Replay
To replay Companies House events from a historical timepoint:
```python
from backend.app.database import SessionLocal
from backend.app.services.ingestion import IngestionService

db = SessionLocal()
ingestion = IngestionService(db)
# Reset cursor to target timepoint
ingestion.update_checkpoint("companies_house_charges_stream", "1000", processed_count=0)
db.close()
```

### Dead-Letter Queue Inspection
Failed or unparseable payloads are recorded in `dead_letter_events` and accessible via:
`GET /api/v1/ingest/dead-letter-queue`

### Recomputing Company Scores
Scores are recomputed dynamically upon arrival of material signals or via API:
`POST /api/v1/companies/{crn}/recompute-score`
