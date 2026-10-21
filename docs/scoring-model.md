# Liquidity Strain Scoring Model (Version 1.0.0)

This document provides the mathematical specification, sub-score weightings, decay dynamics, corroboration rules, and regulatory disclaimers for the **Liquidity Strain Score (LSS)** engine implemented in [`backend/app/services/scoring_engine.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/scoring_engine.py).

---

## 1. Executive Summary & Purpose

The Liquidity Strain Score is an explainable numerical index from `0.0` to `100.0` designed to identify UK limited companies approaching a potential working-capital financing requirement.

The score synthesises four objective data pillars:
1. **Pillar 1: Corporate Filings & Charge Events** (30% weight)
2. **Pillar 2: Commercial Credit Risk & Payment Delinquency** (35% weight)
3. **Pillar 3: Workforce & Operational Dynamics** (20% weight)
4. **Pillar 4: Balance Sheet & Regulatory Filing Cadence** (15% weight)

```
       ┌────────────────────────┐      ┌────────────────────────┐
       │ Pillar 1: Filings      │      │ Pillar 2: Credit / DBT │
       │ Weight: 30%            │      │ Weight: 35%            │
       └───────────┬────────────┘      └───────────┬────────────┘
                   │                               │
                   └───────────────┬───────────────┘
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │ Raw Weighted Base Strain      │
                   └───────────────┬───────────────┘
                                   │
                   ┌───────────────┴───────────────┐
                   ▼                               ▼
       ┌────────────────────────┐      ┌────────────────────────┐
       │ Pillar 3: Workforce    │      │ Pillar 4: Accounts     │
       │ Weight: 20%            │      │ Weight: 15%            │
       └────────────────────────┘      └────────────────────────┘
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │ Temporal Decay & Corroboration│
                   │ Multipliers Applied           │
                   └───────────────┬───────────────┘
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │ Single-Source Cap (Max 49.0)  │
                   │ Contradictory Penalty (-15.0) │
                   └───────────────┬───────────────┘
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │ Final Liquidity Strain Score  │
                   │ (0.0 - 100.0) & Priority Band │
                   └───────────────────────────────┘
```

---

## 2. Pillar Mathematical Formulations

### Pillar 1: Corporate Filings & Charge Releases ($W_1 = 0.30$)
Identifies recent release of fixed/floating charges (e.g. form `MR04`), which frees previously encumbered collateral, creating borrowing headroom.
$$S_1 = \min(100.0, \, 55.0 \cdot N_{\text{MR04}} + 25.0 \cdot N_{\text{charges\_satisfied}})$$
Subject to half-life decay where event age $t$ (days) reduces impact:
$$D_1(t) = e^{-\frac{\ln(2)}{60} \cdot t}$$

### Pillar 2: Commercial Credit & Payment Velocity ($W_2 = 0.35$)
Evaluates trade payment stretch via Days Beyond Terms (DBT) and credit bureau risk indicators.
$$S_2 = \begin{cases} 
0.0 & \text{if DBT} \le 5 \\
2.5 \cdot (\text{DBT} - 5) + 20.0 \cdot N_{\text{CCJ}} & \text{if } 5 < \text{DBT} \le 30 \\
70.0 + 1.0 \cdot (\text{DBT} - 30) + 25.0 \cdot N_{\text{CCJ}} & \text{if DBT} > 30 
\end{cases}$$
If standard commercial credit score ($C \in [1, 100]$) is available:
$$S_2 \leftarrow 0.6 \cdot S_2 + 0.4 \cdot \max(0.0, \, 100.0 - C)$$

### Pillar 3: Workforce & Headcount Dynamics ($W_3 = 0.20$)
Evaluates headcount contraction and recruitment freezes over the trailing 90 days ($\Delta_{\text{staff\_90d}}$ in percent):
$$S_3 = \begin{cases}
0.0 & \text{if } \Delta_{\text{staff\_90d}} \ge 0\% \\
-2.5 \cdot \Delta_{\text{staff\_90d}} & \text{if } -20\% \le \Delta_{\text{staff\_90d}} < 0\% \\
80.0 + (-1.0 \cdot (\Delta_{\text{staff\_90d}} + 20\%)) & \text{if } \Delta_{\text{staff\_90d}} < -20\%
\end{cases}$$

### Pillar 4: Accounting Delinquency & Filing Hygiene ($W_4 = 0.15$)
Penalizes late filing of annual accounts or confirmation statements with Companies House:
$$S_4 = \min(100.0, \, 1.5 \cdot \text{DaysOverdue} + 30.0 \cdot \mathbb{I}_{\text{accounts\_overdue}})$$

---

## 3. Corroboration Dynamics & Hard Safeguards

### A. The Single-Source Hard Cap Rule
To protect commercial brokers from acting on uncorroborated, single-vendor false alarms:
> **Hard Rule**: If strain signals originate from only **one** independent data provider, the final score is mathematically capped at **49.0 / 100.0**, placing the company in the Low-Medium priority band.
>
> To breach **50.0 (High)** or **75.0 (Critical Priority)**, signals must be corroborated across at least **two** distinct providers (e.g. Companies House MR04 filing *and* Creditsafe DBT spike).

Implemented and verified in [`tests/test_scoring_engine.py::test_single_source_uncorroborated_score_capped`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_scoring_engine.py).

### B. Multi-Source Corroboration Multiplier
When independent signals corroborate across multiple providers:
$$M_{\text{corrob}} = \begin{cases}
1.00 & \text{if } K = 1 \text{ provider} \\
1.15 & \text{if } K = 2 \text{ providers} \\
1.25 & \text{if } K \ge 3 \text{ providers}
\end{cases}$$

### C. Contradictory Evidence Penalty
If positive signals exist that indicate financial abundance (e.g. high credit score $C > 85$, rapid headcount growth $\Delta > 15\%$, or zero DBT with zero CCJs), a contradictory evidence deduction is applied:
$$S_{\text{final}} = \max(0.0, \, (S_{\text{raw}} \cdot M_{\text{corrob}}) - 15.0)$$

---

## 4. Priority Categorisation

| Score Range | Priority Band | Recommended Broker Action | SLA / Follow-up Cadence |
| :--- | :--- | :--- | :--- |
| **80.0 – 100.0** | **CRITICAL** | Priority direct outreach; bespoke debt advisory or asset refinance assessment. | Immediate (< 24 hours) |
| **60.0 – 79.9** | **HIGH** | Full credit file review, director filings verification, introductory working-capital consultation. | Within 48 hours |
| **40.0 – 59.9** | **MEDIUM** | Add to watchlist; monitor subsequent monthly DBT and Gazette notices. | Weekly review |
| **0.0 – 39.9** | **LOW / STABLE** | Automated background monitoring only; no direct outreach warranted. | Monthly refresh |

---

## 5. Regulatory Disclaimers & Legal Compliance

> [!WARNING]
> **NOT A CREDIT RATING OR CREDIT SCORE**
> 
> The Liquidity Strain Score produced by this platform:
> 1. **Is NOT a credit rating** under UK Regulation (EC) No 1060/2009 (Credit Rating Agencies).
> 2. **Is NOT an automated credit decision** under Section 14 of the UK Data Protection Act 2018 or Article 22 of UK GDPR.
> 3. Does not assess consumer creditworthiness or provide individual credit scoring.
> 4. Serves purely as an investigative business development prioritization metric for commercial brokers.
> 5. All commercial decisions must be backed by official company filings, certified accounts, and human underwriting review.

---

## 9. Adaptive Opportunity Assessment & Dual-Layer Machine Learning Architecture

The platform operates a **dual-layer scoring architecture**:
1. **Deterministic Rules Engine (`scoring_engine.py`, `opportunity_assessment.py`)**: The authoritative, fully explainable baseline and production fallback.
2. **Supervised Machine Learning Layer (`backend/app/ml/`)**: Runs in shadow mode to evaluate whether statistical learning can improve top-of-funnel broker precision without sacrificing calibration or stability.

### Dual-Layer Operational Guarantees
- **Explainability**: If the ML layer is promoted, the rules-based score and contribution breakdown remain accessible on every company profile for human underwriting and broker explanation.
- **Fail-Safe Fallback**: If ML prediction libraries or models are unavailable or uncalibrated, the platform seamlessly defaults to the deterministic rules engine without service interruption.
- **Canonical Feature Alignment**: Both training and inference consume the exact same `FEATURE_SPEC` ([`backend/app/ml/features.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/ml/features.py)) with explicit missingness flags, preventing training/serving skew.
- **Bi-Temporal Point-in-Time Isolation**: Features are strictly computed as of the snapshot cutoff date. Right-censored companies are excluded from training datasets.
- **Statutory Proxy vs Broker Conversion**:
  - `new_charge_12m`: Measures broader SME commercial finance DEMAND from Companies House statutory charge filings.
  - `broker_funded`: Measures actual broker funnel conversion.
- **Head-to-Head Walk-Forward Validation**: Candidate ML models (Regularized Logistic Regression, Monotonic HistGradientBoosting, Calibrated HistGradientBoosting) are evaluated against the rules baseline on identical out-of-sample temporal slices with disjoint company partitioning.
- **Statistical Promotion Gate**: Candidate models can only be promoted to active scoring via the manual Admin workflow if cluster bootstrap confidence intervals demonstrate statistically significant improvements in Top-10 Precision and Brier score over the deterministic rules baseline.
- **Shadow Prediction Persistence**: Every generated assessment persists shadow ML scores in the `model_predictions` table for continuous retrospective auditing.

