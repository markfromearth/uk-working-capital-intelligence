# Data Protection, UK GDPR & Retention Policy

This document details the data protection governance, legal basis, data minimization controls, PECR compliance, and retention schedules governing the UK Working-Capital Intelligence & Prospect Scoring Platform under the **UK General Data Protection Regulation (UK GDPR)** and the **Data Protection Act 2018 (DPA 2018)**.

---

## 1. Regulatory Context & Scope

The platform is designed exclusively for **B2B commercial finance intelligence** targeting corporate legal entities (UK Private and Public Limited Companies, Limited Liability Partnerships). 

While corporate entities do not have personal data privacy rights under UK GDPR, personal data is inevitably processed in connection with:
1. **Identified Company Officers**: Registered directors, company secretaries, and Persons with Significant Control (PSC) as published in the statutory Companies House register.
2. **Platform Users**: Commercial finance brokers, prospecting analysts, and administrators.
3. **Sole Traders & Unincorporated Partnerships**: Where data may overlap with consumer credit or individual personal data.

---

## 2. Lawful Basis for Processing (Article 6 UK GDPR)

For the processing of personal data relating to corporate officers and directors, the platform relies on:

### **Legitimate Interests (Article 6(1)(f) UK GDPR)**
- **Purpose Test**: To enable commercial finance brokers to identify UK businesses that may benefit from alternative working-capital funding, thereby supporting legitimate UK commerce and liquidity.
- **Necessity Test**: Processing corporate officer names and public statutory filings is strictly necessary to establish corporate identity, verify borrowing capacity, and direct relevant business inquiries.
- **Balancing Test**: The data processed is already published on the statutory public register (Companies House) by legal obligation. The intrusion upon individual privacy is minimal, restricted to professional business capacities, and outweighed by the economic utility of facilitating commercial credit.

> [!NOTE]
> The platform does not process Special Category Data (Article 9) or criminal offense data (Article 10).

---

## 3. Data Minimization & Boundary Rules

To prevent unlawful capture of consumer or non-corporate personal data:
1. **Exclusion of Sole Traders**: The platform rejects any entity that is not an incorporated UK entity with a valid 8-character Companies House Company Registration Number (CRN).
2. **Exclusion of Home Residential Addresses**: Where a director's registered correspondence address matches a residential indicator or where disclosure restrictions apply under section 243 of the Companies Act 2006, residential addresses are omitted.
3. **Restricted Contact Capture**: Contact points are limited to corporate domains (`@company.co.uk`) and registered corporate office addresses.

---

## 4. Privacy and Electronic Communications Regulations (PECR) Compliance

When commercial finance brokers initiate outreach based on platform intelligence, they must adhere to UK PECR rules governing corporate subscribers:

| Subscriber Category | Definition under PECR | Direct Marketing Rule |
| :--- | :--- | :--- |
| **Corporate Subscriber** | Limited companies (Ltd), Public Limited Companies (Plc), LLPs, Scottish partnerships. | B2B marketing emails/calls do not require prior consent (opt-in), but: <br>1. Must clearly identify the sender.<br>2. Must provide a functional opt-out / unsubscribe mechanism.<br>3. Must honor suppression lists immediately. |
| **Individual Subscriber** | Sole traders, standard partnerships, individual consumers. | **STRICTLY EXCLUDED** from the platform. Prior consent (opt-in) required under PECR. |

### Broker Outreach Rules Built into the Platform:
- The system generates an evidence dossier to support transparent, justifiable B2B contact.
- Prospects marked as `DO_NOT_CONTACT` or `UNQUALIFIED` are instantly added to the system-wide suppression register.

---

## 5. Data Retention & Archival Schedules

In compliance with the UK GDPR storage limitation principle (Article 5(1)(e)):

| Data Category | Target Store | Retention Period | Post-Retention Action |
| :--- | :--- | :--- | :--- |
| **Raw Filings & Stream Events** | `raw_signals` | 24 months from ingestion | Hard delete or roll-up into monthly aggregate statistical counters |
| **Feature Store Snapshots** | `feature_snapshots` | 36 months | Compressed archival for backtest model verification |
| **Prospect Dossiers & Scores** | `scoring_history` | Active prospect lifetime + 12 months | Anonymised score record retained; officer references purged |
| **Audit Logs & Tamper Chain** | `audit_logs` | 7 years (Statutory compliance requirement) | Cryptographic archive maintained for regulatory review |
| **Dead Letter Queue (Quarantine)** | `dead_letter_events` | 90 days | Unresolved records purged automatically |

---

## 6. Data Subject Rights & Opt-Out Handling

Under UK GDPR Articles 15–21, individuals (directors/officers) have rights regarding their personal data:
1. **Right to Object (Article 21)**: If a corporate officer objects to processing, the company can be placed on an immediate suppression list preventing broker display or score generation.
2. **Right of Access (Article 15)**: The platform maintains an audit trail showing all signals ingested regarding the associated corporate entity.
3. **Right to Rectification (Article 16)**: Where public filings are amended or disputed, signals can be marked as `DISPUTED` or `SUPERSEDED`, instantly removing them from scoring calculations.
