# CH-Only Invoice Finance Warden: Plan Audit & Refinement Report

**Review Board**: Adversarial Senior FinTech Architect & Credit Risk Review  
**Subject**: Architectural Blueprint (`docs/ch-warden-mvp-architecture.md`)  
**Status**: APPROVED WITH MANDATORY REFINEMENTS

---

## 1. Vulnerability & Edge-Case Findings

### Finding 1: The "Active Debenture Trap" in White Space Calculation
* **Risk**: A company might have a satisfied Barclays debenture from 6 months ago, but subsequently took out an omnibus debenture with NatWest or an asset-based lender 1 month ago.
* **Refinement**: "White Space" must require that **ZERO active floating charges or all-monies debentures exist** on the company's charges register. Any existing active floating charge instantly voids the White Space trigger.

### Finding 2: High Street Bank Name Permutations
* **Risk**: High Street banks appear in `persons_entitled` with dozens of corporate registry variations (e.g. `NATIONAL WESTMINSTER BANK PLC`, `NATWEST`, `THE ROYAL BANK OF SCOTLAND PLC`, `BARCLAYS BANK UK PLC`, `HSBC UK BANK PLC`, `LLOYDS BANK PLC`, `BANK OF SCOTLAND PLC`, `SANTANDER UK PLC`).
* **Refinement**: Maintain a compiled regex bank matching canonized entity stems (`barclays`, `natwest|national westminster`, `hsbc`, `lloyds`, `bank of scotland`, `santander`, `clydesdale|virgin money`).

### Finding 3: iXBRL Namespace Fragmentation
* **Risk**: UK corporate filings use varying XBRL taxonomies (`uk-gaap`, `uk-ifrs`, `frc`, `uk-core`). A strict namespace query like `soup.find('uk-gaap:debtors')` will miss over 50% of digital filings.
* **Refinement**: Match XBRL elements using namespace-agnostic tag queries inspecting the `name` attribute or local XML tag name matching `(debtors|tradeandotherreceivables|currentassets|cashandcashequivalents|creditorsdueone)*` case-insensitively.

### Finding 4: Accounting Parentheses Handling
* **Risk**: Statutory balance sheets use parentheses to indicate negative balances or liabilities (e.g. `(£124,500)`). A naive string-to-float regex will strip the parentheses and treat liabilities as positive assets, breaking accounting reconciliation.
* **Refinement**: In both iXBRL and PDF extraction, convert any value wrapped in `(...)` or containing `-` into a negative float.

---

## 2. Refined Architectural Delta Contract

1. **Indicator Engine**:
   - `check_white_space(charges: List[dict]) -> Tuple[bool, Optional[str]]`: Asserts no active floating charges exist, and latest satisfaction was within 180–730 days.
   - `check_refinance_window(charges: List[dict]) -> Tuple[bool, Optional[str]]`: Detects active charges registered by non-clearing invoice finance providers aged 24–48 months.
2. **Accounts Fetcher**:
   - Explicit `follow_redirects=False` in initial request, followed by URL inspection; if redirected to S3 (`*.amazonaws.com`), perform GET with clean headers (no `Authorization`).
3. **iXBRL Parser**:
   - Namespace-agnostic element scanner with automatic `(...)` negative-number parser.
4. **Validation**:
   - Balance sheet equation check with a statutory rounding tolerance of $\pm £2.0$.

---
**Verdict**: Implement Stage 1 and Stage 2 applying these 4 refinements.
