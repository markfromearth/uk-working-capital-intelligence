# Adaptive Finance Intelligence Engine Specification

## 1. Overview and Commercial Rationale

The **Adaptive Finance Intelligence Engine** extends the core Liquidity Strain Scoring platform with active investigation economics, commercial opportunity sizing, and continuous feedback learning.

Rather than treating data ingestion as a static, uncurated batch process, the Adaptive Engine treats data queries as economic capital allocation decisions. It balances the commercial value of resolving uncertainty against external provider API query costs, data freshness decay, and contractual data-use boundaries.

```
                           +-------------------------------------+
                           | 1. Signal Detection & Normalisation |
                           +------------------+------------------+
                                              |
                                              v
                           +-------------------------------------+
                           | 2. Opportunity Assessment Engine    |
                           |   - P(Qualified Opportunity)        |
                           |   - P(Funded Facility)              |
                           |   - Product Suitability (ID/AR/TL)  |
                           +------------------+------------------+
                                              |
                                              v
                           +-------------------------------------+
                           | 3. Value-of-Information (EVOI)      |
                           |    EVOI = E[ΔU] - C_API             |
                           |    Decisions: INVESTIGATE / DEFER / |
                           |               STOP / ESCALATE       |
                           +------------------+------------------+
                                              |
                                              v
                           +-------------------------------------+
                           | 4. Controlled Execution Registry    |
                           |   - Rate Limits & Circuit Breakers  |
                           |   - Transparent Query Costs         |
                           +------------------+------------------+
                                              |
                                              v
                           +-------------------------------------+
                           | 5. Continuous Funnel Learning Loop  |
                           |   - Funnel Touchpoints Recorded     |
                           |   - ML Shadow Mode (v1.1.0-shadow)  |
                           |   - Isotonic Probability Calibration|
                           +-------------------------------------+
```

---

## 2. Value-of-Information (EVOI) Mathematics

Commercial finance brokers operate under strict margin constraints. Querying external commercial credit bureaus (e.g., Experian, Creditsafe) incurs direct API costs (£0.10 to £3.00+ per query).

The engine computes the **Expected Value of Information (EVOI)**:

$$\text{EVOI} = P(\text{Action Threshold Change}) \times \mathbb{E}[\Delta \text{Commission}] - C_{\text{API}}$$

Where:
- $P(\text{Action Threshold Change})$: Estimated probability that the posterior evidence distribution shifts the broker's prioritization tier (e.g. from Snooze to Priority Outreach).
- $\mathbb{E}[\Delta \text{Commission}]$: Expected commercial upside (£) of funded facility arrangement (defaulting to 2.0% of facility facility sizing).
- $C_{\text{API}}$: Transparent contract cost of the candidate provider tool.

### Decision Rules:
1. **`INVESTIGATE`**: $\text{EVOI} > 0.0$ and expected entropy reduction $\ge 0.15$, within remaining daily budget. Tool executes automatically or with one-click authorization.
2. **`DEFER`**: $C_{\text{API}} > \text{Budget}_{\text{Remaining}}$ (default company daily budget: £5.00). Deferred until budget reset window.
3. **`STOP`**: Evidence was already retrieved within the past 24 hours ($\text{EVOI} = 0.0$), or baseline filings are already conclusive.
4. **`ESCALATE_HUMAN_REVIEW`**: High prior uncertainty ($\ge 0.60$) combined with substantial query fees ($\ge £2.00$). Requires explicit human broker approval.

---

## 3. Approved Tool Registry & Circuit Breakers

All investigation capabilities are declared in the `ApprovedToolRegistry` with explicit fee accounting, rate limiting, and failure containment:

| Tool Identifier | Provider | Query Fee (£) | Rate Limit | Data Use Policy | Circuit Breaker |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `companies_house_charges_refresh` | Companies House | £0.00 | 120 / min | `statutory_public_register_b2b_only` | 3 fails / 60s cooldown |
| `companies_house_profile_lookup` | Companies House | £0.00 | 120 / min | `statutory_public_register_b2b_only` | 3 fails / 60s cooldown |
| `creditsafe_deep_financials` | Creditsafe | £2.50 | 60 / min | `commercial_credit_assessment_b2b` | 3 fails / 60s cooldown |
| `experian_delinquency_trace` | Experian | £3.00 | 50 / min | `commercial_credit_assessment_b2b` | 3 fails / 60s cooldown |
| `workforce_hiring_velocity` | Workforce Intelligence | £0.10 | 100 / min | `public_workforce_aggregates_only` | 3 fails / 60s cooldown |

### Circuit Breaker States:
- **`CLOSED`**: Healthy operation; tool queries proceed directly.
- **`OPEN`**: 3 consecutive query failures trip the breaker. Subsequent requests fail fast for 60 seconds without consuming API credits.
- **`HALF-OPEN`**: Following cooldown expiry, a single probe execution is allowed to test provider recovery.

---

## 4. Commercial Opportunity Assessment & Product Fit

The engine estimates separate commercial dimensions:

1. **$P(\text{Qualified Opportunity})$**: Likelihood that the SME is actively approaching a working-capital requirement, driven by recent MR04 charge releases, Days Beyond Terms (DBT) expansion, and rapid payroll growth.
2. **$P(\text{Conversion to Funded Facility})$**: Credit-hygiene-adjusted conversion likelihood, discounted for severe adverse insolvency judgments or winding-up petitions.
3. **Estimated Facility Requirement**: Headcount scale and operational burn sizing:
   $$\text{Facility}_{\text{Est}} = \max(£50,000, \, \text{Headcount} \times £12,500)$$
4. **Product Suitability Matrix**:
   - **Invoice Discounting / Factoring**: High when DBT is elevated ($\ge 15$ days) and customer ledger stretch is evident.
   - **Asset Refinance (Machinery / Fleet)**: High when prior fixed charges are satisfied and plant/equipment assets can be unencumbered.
   - **Unsecured Term Loan**: High when cashflow is positive, adverse credit is zero, and rapid headcount growth requires growth capital.

---

## 5. Machine Learning Architecture & Production Promotion Gate

The platform employs a rigorously evaluated machine learning layer that operates alongside the deterministic rules engine. The rules engine remains the explainable baseline and production fallback.

### A. Canonical Feature Specification (`FEATURE_SPEC`)
- Defined once in [`backend/app/ml/features.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/features.py) with immutable SHA-256 spec hash (`get_feature_spec_hash()`).
- Encapsulates 10 continuous domain features and 5 explicit binary missingness indicators (`headcount_growth_90d_pct_is_missing`, `dbt_current_days_is_missing`, `credit_score_drop_90d_is_missing`, `age_latest_charge_satisfaction_days_is_missing`, `headcount_current_is_missing`).
- Enforces monotonic direction constraints across tree algorithms (e.g. higher DBT and credit score drops strictly increase predicted probability; older charge satisfaction strictly decreases predicted probability).

### B. Point-in-Time Dataset Construction & Censoring
- Built in [`backend/app/ml/dataset.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/dataset.py).
- Strictly **one row per `(crn, snapshot_date)`**.
- Features are strictly computed as-of `snapshot_date` using bi-temporal filtering (`observation_timestamp <= snapshot_date` and `ingestion_timestamp <= snapshot_date`), preventing future signal leakage.
- Multiple funnel updates for a company are collapsed into a single milestone event.
- **Right-censoring**: Snapshots where `snapshot_date + horizon > as_of_reference` are strictly dropped from training datasets, as their final outcome is not yet knowable. Censored counts are reported transparently.

### C. Proxy-Label Pipeline vs Broker Funnel
- Implemented in [`backend/app/ml/proxy_pipeline.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/proxy_pipeline.py).
- **Statutory Proxy (`new_charge_12m`)**: Evaluates whether a company registered a new charge with Companies House within the subsequent 12 months.
  > [!IMPORTANT]
  > The statutory proxy measures **commercial finance DEMAND**, not broker conversion. A company may take finance directly from a high-street bank or alternative lender without broker engagement.
- **Broker Target (`broker_funded`)**: Evaluates verified broker conversion (`FACILITY_FUNDED`).

### D. Honest Walk-Forward Evaluation & Disjoint Company Splitting
- Implemented in [`backend/app/ml/train.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/train.py).
- Evaluates candidate models:
  1. `regularized_logistic_regression`: Robust standard scaling + median imputation + L2 regularization.
  2. `monotonic_hist_gradient_boosting`: Non-linear gradient boosting with monotonic constraints.
  3. `calibrated_hist_gradient_boosting`: HistGradientBoosting calibrated via sigmoid on a held-out temporal validation slice using `FrozenEstimator` (never fitted on training data).
- **Temporal Walk-Forward + GroupKFold**: Partitions distinct companies into disjoint sets. No company appears in both train and test splits, and test records strictly occur after training snapshot cutoffs.
- **Head-to-Head Baseline Comparison**: Out-of-sample metrics (ROC-AUC, PR-AUC, Brier score, and Top-K Precision / Lift for $k=10, 25, 50$) are evaluated simultaneously for candidate models and the deterministic rules baseline on identical test rows.
- **Training Refusal Guard**: Refuses to fit or promote models if the training set contains fewer than 200 distinct companies or fewer than 30 positive outcomes.

### E. Shadow Prediction Logging & Promotion Gate
- Persisted in the `model_predictions` table via [`backend/app/models/model_prediction.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/model_prediction.py).
- Every assessment logs candidate ML predictions, model version, and feature vector in shadow mode alongside the rules score.
- **Cluster Bootstrap Promotion Gate** ([`backend/app/ml/promotion.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/promotion.py)):
  - Evaluates cluster bootstrap confidence intervals (resampling by company, 1,000 resamples) for $\Delta_{\text{top\_10\_precision}}$ and $\Delta_{\text{brier}}$.
  - A candidate model is eligible for promotion ONLY IF the 95% bootstrap CI shows statistically significant non-negative improvement over the rules baseline.
  - Manual promotion endpoint `POST /api/v1/adaptive/ml/promote` enforces ADMIN role and logs immutable tamper-evident audit records.

---

## 6. Empirical EVOI Learning & Run Outcome Tracking

Rather than relying on static heuristic constants for information gain, the platform implements empirical EVOI learning via [`backend/app/ml/evoi_learning.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/evoi_learning.py) and the `investigation_run_logs` table:

1. **Pre/Post State Tracking**: Captures prior action, prior risk tier, prior uncertainty, and feature missingness pattern before tool execution. Following tool completion and signal ingestion, records posterior action, tier, and uncertainty.
2. **Action Shift Detection**: Evaluates whether the new evidence materially changed the broker's recommended action (`action_changed = True`).
3. **Bayesian-Smoothed Learning**:
   $$\hat{P}(\text{Action Change} \mid \text{tool}, \text{pattern}) = \frac{N_0 \cdot P_0 + K_{\text{changed}}}{N_0 + N_{\text{runs}}}$$
   Where $N_0 = 10$ is the pseudo-prior weight and $P_0$ is the prior heuristic.
4. **Fallback Safeguard**: When fewer than 20 executed runs exist for a tool, the engine safely falls back to the domain prior heuristic.

---

## 7. End-to-End API Routes

All endpoints are registered under `/api/v1/adaptive`:

- `GET /api/v1/adaptive/tools`: List approved tools with costs and rate limits.
- `POST /api/v1/adaptive/investigate/evaluate/{crn}`: Evaluate EVOI across candidate tools.
- `POST /api/v1/adaptive/investigate/execute`: Execute approved tool within budget and log outcome.
- `GET /api/v1/adaptive/investigations/{crn}`: Query investigation history and rationale.
- `GET /api/v1/adaptive/assessments/{crn}`: Get latest commercial opportunity assessment and shadow ML predictions.
- `POST /api/v1/adaptive/outcomes`: Record broker conversion funnel feedback.
- `GET /api/v1/adaptive/outcomes/metrics`: Commercial conversion KPIs and net ROI.
- `GET /api/v1/adaptive/ml/status`: Inspect ML shadow mode volume, outcome counts, and promotion gate criteria.
- `POST /api/v1/adaptive/ml/promote`: Promote candidate ML model with admin authentication and audit logging.
- `POST /api/v1/adaptive/ml/train`: Train calibrated machine learning model (Admin role required).
