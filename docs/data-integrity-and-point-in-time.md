# Data Integrity, Bi-Temporal Model & Point-In-Time Correctness

This document describes the architectural safeguards implemented in the UK Working-Capital Intelligence & Prospect Scoring Platform to ensure absolute historical data integrity, prevent lookahead bias (data leakage) in feature engineering, and guarantee legal and audit compliance.

---

## 1. The Bi-Temporal Ingestion Model

A fundamental flaw in naive intelligence platforms is querying signals solely based on when the event occurred in the real world (`observation_timestamp`). This introduces severe **lookahead bias**: backtested scores computed for a historical date would incorporate data that arrived days or weeks later.

The platform enforces a strict **bi-temporal model** across all raw signals and feature calculations:

| Timestamp Attribute | Definition | Immutability |
| :--- | :--- | :--- |
| `observation_timestamp` | The point in time when the event occurred in the physical world (e.g. charge satisfaction date registered at Companies House, date of CCJ judgment). | Immutable from source document. |
| `ingestion_timestamp` | The point in time when the platform backend received, validated, and persisted the record into the database. | Server UTC timestamp assigned at `INSERT`. |

```
Time Axis ───►
      │
      ├── [T1] Event Occurs in the World (observation_timestamp = T1)
      │
      ├── [T2] Target "As-Of" Date for Backtesting / Historical Evaluation
      │        (If querying as_of = T2: event NOT visible yet!)
      │
      └── [T3] Event Ingested by Platform (ingestion_timestamp = T3)
               (Event becomes visible ONLY for queries where as_of >= T3)
```

### Bi-Temporal Query Filter

In [`backend/app/services/feature_store.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/services/feature_store.py), every signal retrieval enforces:

```python
stmt = (
    select(RawSignal)
    .where(
        RawSignal.entity_id == entity_id,
        RawSignal.observation_timestamp <= as_of,
        RawSignal.ingestion_timestamp <= as_of,
        RawSignal.status.in_(["PROCESSED", "INGESTED"])
    )
    .order_by(RawSignal.observation_timestamp.desc())
)
```

If `ingestion_timestamp > as_of`, the record is strictly excluded, ensuring zero leakage of late-arriving filings or delayed credit bureau updates.

---

## 2. Signal Validity, Disputed & Superseded Signals

Commercial credit reports and filings are frequently subjected to revisions, court judgment set-asides, or formal disputes. 

To maintain data integrity:
1. **Signal Status Lifecycle**:
   - `INGESTED`: Raw payload received and queued.
   - `PROCESSED`: Normalized and incorporated into entity feature calculations.
   - `SUPERSEDED`: Replaced by a more recent official revision (e.g. amended company accounts or updated charge registration).
   - `DISPUTED`: Marked under review following customer dispute or bureau notification.
   - `QUARANTINED`: Malformed payload held in the Dead Letter Queue.
2. **Filtering in Feature Store**:
   - All `SUPERSEDED` and `DISPUTED` signals are explicitly excluded from feature extraction and scoring.
   - Verified via automated test: [`test_disputed_and_superseded_signals_excluded`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_point_in_time_leakage_remediation.py).

---

## 3. Strict Metric Separation: Days Beyond Terms (DBT) vs Days Sales Outstanding (DSO)

A common domain error in working-capital software is conflating **Days Beyond Terms (DBT)** with **Days Sales Outstanding (DSO)**:

| Metric | Full Name | Domain Meaning | Directional Meaning for Liquidity |
| :--- | :--- | :--- | :--- |
| **DBT** | Days Beyond Terms | The average number of days a company takes to pay its suppliers *after* the invoice due date has passed. | High/Increasing DBT indicates trade credit stretch, cash flow strain, or working-capital shortfall. |
| **DSO** | Days Sales Outstanding | The average number of days it takes for the company to *collect* payment from its own customers. | High DSO reflects customer collection delays, which may cause secondary liquidity strain, but does not measure delinquency to creditors. |

### Architectural Enforcement:
1. In [`backend/app/models/feature.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/models/feature.py) and [`backend/app/schemas/feature.py`](file:///Users/trinity/Documents/antigravity/eager-planck/backend/app/schemas/feature.py), `dbt_current` and `dso_current` are distinct columns and schema fields.
2. The scoring engine evaluates `dbt_current` within **Pillar 2 (Commercial Credit Risk & Delinquency)** as a direct strain indicator, while `dso_current` is retained strictly as an operating efficiency metric.
3. Unit verification: [`test_strict_dbt_vs_dso_separation`](file:///Users/trinity/Documents/antigravity/eager-planck/tests/test_credit_and_workforce_adapters.py).

---

## 4. Signal Deduplication by Event Key

Multiple webhooks or repeated stream polling can transmit duplicate records for the same corporate event.

To prevent artificial signal amplification:
1. During normalisation, a deterministic `payload_hash` is computed using `SHA-256(canonical_json_string)`.
2. Signals with identical `(source_provider, raw_event_id)` or `payload_hash` are deduplicated prior to feature aggregation.
3. If duplicate events exist in the database, the feature extractor groups by `raw_event_id` and takes the single authoritative record with the earliest ingestion timestamp.

---

## 5. Explicit Data Availability Indicators

To prevent zero-filling or null-imputation from biasing the Liquidity Strain Score:
1. Feature snapshots contain boolean availability indicators:
   - `dbt_data_available`
   - `credit_score_data_available`
   - `workforce_data_available`
2. If a provider's data is missing or unavailable as of the target date, the engine distinguishes **absence of evidence** from **evidence of absence**. Missing pillars are omitted from corroboration counts rather than scored as zero strain.
