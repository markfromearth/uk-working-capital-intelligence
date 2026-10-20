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
- **Restrained B2B Fintech UI**: Responsive data-dense dashboard, prioritized prospect table, multi-parameter filtering, interactive company profiles, and human review/dispute workflow.

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

The platform includes 31 comprehensive tests spanning unit, integration, entity resolution, point-in-time correctness, and controlled failure-detection tests:

```bash
source .venv/bin/activate
PYTHONPATH=. pytest -v tests/
```

### Verification Coverage
1. `tests/test_crn_normalizer.py`: Numeric padding, Scottish `SC`, Northern Irish `NI`, LLP `OC`, and error handling.
2. `tests/test_companies_house_adapter.py`: MR04 charge parsing, full vs partial scope detection, stream checkpoint persistence, and resume cursor recovery.
3. `tests/test_credit_and_workforce_adapters.py`: Creditsafe webhook HMAC verification, replay protection, strict DBT vs DSO separation, and headcount growth/acceleration calculations.
4. `tests/test_entity_resolution.py`: Exact CRN matching, high-confidence fuzzy matching with postcode boost, and manual review routing for ambiguous candidates (score 0.65–0.89).
5. `tests/test_feature_store_point_in_time.py`: Historical point-in-time isolation proving no future signal leakage.
6. `tests/test_scoring_engine.py`: Multi-source corroboration, single-source caps (capped at 49.0), and contradictory evidence dampening.
7. `tests/test_llm_explainer.py`: Strict schema validation and rejection of hallucinated signal IDs.
8. `tests/test_api_and_review_workflow.py`: End-to-end API verification, dashboard metrics, profile retrieval, and broker review logging.
9. `tests/test_controlled_failures.py`: Demonstrates that critical tests actively detect invalid conditions.

---

## 4. Configuration & Environment Variables

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
