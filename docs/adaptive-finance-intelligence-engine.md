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

## 5. Machine Learning Architecture: Shadow Mode (`v1.1.0-ml-shadow`)

To preserve production stability and comply with model risk management standards, machine learning operates in **Shadow Mode**:

- **Model Version**: `v1.1.0-ml-shadow`
- **Algorithm**: `HistGradientBoostingClassifier` wrapped in `CalibratedClassifierCV` (isotonic regression).
- **Target Leakage Guarantee**: Features are strictly computed using point-in-time snapshots as of the historical observation timestamp preceding the funnel outcome.
- **Safety Fallback**: If fewer than 6 verified commercial outcomes exist in the database, the model operates on deterministic empirical scoring heuristics.
- **Reliability Target**: Brier score calibration metric $< 0.25$ and ROC-AUC tracking.

---

## 6. End-to-End API Routes

All endpoints are registered under `/api/v1/adaptive`:

- `GET /api/v1/adaptive/tools`: List approved tools with costs and rate limits.
- `POST /api/v1/adaptive/investigate/evaluate/{crn}`: Evaluate EVOI across candidate tools.
- `POST /api/v1/adaptive/investigate/execute`: Execute approved tool within budget.
- `GET /api/v1/adaptive/investigations/{crn}`: Query investigation history and rationale.
- `GET /api/v1/adaptive/assessments/{crn}`: Get latest commercial opportunity assessment and ML shadow prediction.
- `POST /api/v1/adaptive/outcomes`: Record broker conversion funnel feedback.
- `GET /api/v1/adaptive/outcomes/metrics`: Commercial conversion KPIs and net ROI.
- `POST /api/v1/adaptive/ml/train`: Train calibrated machine learning model (Admin role required).
