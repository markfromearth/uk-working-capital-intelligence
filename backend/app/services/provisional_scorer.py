"""
Dynamic Provisional Scorer & Gatekeeper for CH-Only Invoice Finance Warden.
Calculates transparent 0-100 scores based on user-defined weights,
applies lending archetype knockouts, collects retrievable primary sources,
and gates companies for Stage 2 Quant extraction and Stage 3 Intelligence Refinement.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.schemas.warden import (
    IndicatorBreakdown,
    LendingArchetype,
    PrimarySourceReference,
    ScreeningResult,
)
from backend.app.services.indicator_engine import (
    evaluate_admin_delinquency,
    evaluate_refinance_window,
    evaluate_tm01_cluster,
    evaluate_white_space,
    is_b2b_eligible,
)

DEFAULT_INDICATOR_WEIGHTS = {
    "white_space": 35.0,
    "refinance_window": 25.0,
    "tm01_cluster": 15.0,
    "admin_delinquency": 10.0,
    "b2b_sector_fit": 15.0,
}

DEFAULT_TOGGLES = {
    "white_space": True,
    "refinance_window": True,
    "tm01_cluster": True,
    "admin_delinquency": True,
    "b2b_sector_fit": True,
}


class ProvisionalScorer:
    """Calculates dynamic 0-100 provisional invoice finance suitability scores."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        toggles: Optional[Dict[str, bool]] = None,
        archetype: LendingArchetype = LendingArchetype.PRIME_INVOICE_DISCOUNTING,
        min_gate_threshold: float = 65.0,
    ):
        self.weights = weights or dict(DEFAULT_INDICATOR_WEIGHTS)
        self.toggles = toggles or dict(DEFAULT_TOGGLES)
        self.archetype = archetype
        self.min_gate_threshold = min_gate_threshold

    def _get_normalized_weights(self) -> Dict[str, float]:
        """Normalize active toggled weights so the maximum possible score is 100.0."""
        active_sum = sum(
            self.weights.get(k, 0.0)
            for k, enabled in self.toggles.items()
            if enabled
        )
        if active_sum <= 0:
            return {k: 0.0 for k in self.weights}

        return {
            k: (self.weights.get(k, 0.0) / active_sum) * 100.0 if self.toggles.get(k, False) else 0.0
            for k in self.weights
        }

    def score_company(
        self,
        crn: str,
        profile: Dict[str, Any],
        charges: List[Dict[str, Any]],
        filings: List[Dict[str, Any]],
        as_of: Optional[datetime] = None,
    ) -> ScreeningResult:
        """Run Stage 1 evaluation and return a typed ScreeningResult with primary sources."""
        now = as_of or datetime.now(timezone.utc)
        company_name = profile.get("company_name", f"Company {crn}")
        sic_codes = profile.get("sic_codes", [])

        # 1. B2B Sector Check
        b2b_ok, b2b_reason, b2b_source = is_b2b_eligible(sic_codes, crn=crn)

        # 2. Extract Indicators
        ws_ok, ws_factor, ws_reason, ws_source = evaluate_white_space(charges, as_of=now, crn=crn)
        refin_ok, refin_factor, refin_reason, refin_source = evaluate_refinance_window(charges, as_of=now, crn=crn)
        tm01_ok, tm01_count, tm01_reason, tm01_source = evaluate_tm01_cluster(filings, as_of=now, crn=crn)
        delinq_ok, gazette_or_insolvent, delinq_reason, delinq_source = evaluate_admin_delinquency(profile, filings, crn=crn)

        # 3. Check Hard Knockouts
        if self.archetype == LendingArchetype.PRIME_INVOICE_DISCOUNTING and gazette_or_insolvent:
            knockout_source = delinq_source or PrimarySourceReference(
                source_type="COMPANIES_HOUSE_PROFILE",
                source_id="HARD_KNOCKOUT",
                title=f"Disqualifying Strike-off/Insolvency Status",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}",
                excerpt=delinq_reason,
            )
            audit = [
                IndicatorBreakdown(
                    indicator="hard_knockout",
                    weight=0.0,
                    awarded=0.0,
                    triggered=True,
                    evidence=f"Disqualified under Prime rules: {delinq_reason}",
                    primary_source=knockout_source,
                )
            ]
            return ScreeningResult(
                crn=crn,
                company_name=company_name,
                sic_codes=sic_codes,
                is_b2b_eligible=b2b_ok,
                provisional_score=0.0,
                passed_gate=False,
                knockout_applied=True,
                knockout_reason=delinq_reason,
                audit_trail=audit,
                primary_sources=[knockout_source],
            )

        # 4. Compute Normalized Scores & Collate Primary Sources
        norm_weights = self._get_normalized_weights()
        audit_trail: List[IndicatorBreakdown] = []
        primary_sources: List[PrimarySourceReference] = []
        total_score = 0.0

        # White Space
        ws_weight = norm_weights.get("white_space", 0.0)
        ws_awarded = round(ws_weight * ws_factor, 1) if ws_ok else 0.0
        total_score += ws_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="white_space",
                weight=round(ws_weight, 1),
                awarded=ws_awarded,
                triggered=ws_ok,
                evidence=ws_reason,
                primary_source=ws_source,
            )
        )
        if ws_source and ws_ok:
            primary_sources.append(ws_source)

        # Refinance Window
        refin_weight = norm_weights.get("refinance_window", 0.0)
        refin_awarded = round(refin_weight * refin_factor, 1) if refin_ok else 0.0
        total_score += refin_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="refinance_window",
                weight=round(refin_weight, 1),
                awarded=refin_awarded,
                triggered=refin_ok,
                evidence=refin_reason,
                primary_source=refin_source,
            )
        )
        if refin_source and refin_ok:
            primary_sources.append(refin_source)

        # TM01 Resignation Cluster
        tm01_weight = norm_weights.get("tm01_cluster", 0.0)
        tm01_awarded = round(tm01_weight, 1) if tm01_ok else 0.0
        total_score += tm01_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="tm01_cluster",
                weight=round(tm01_weight, 1),
                awarded=tm01_awarded,
                triggered=tm01_ok,
                evidence=tm01_reason,
                primary_source=tm01_source,
            )
        )
        if tm01_source and tm01_ok:
            primary_sources.append(tm01_source)

        # Administrative Delinquency
        admin_weight = norm_weights.get("admin_delinquency", 0.0)
        admin_awarded = round(admin_weight, 1) if delinq_ok else 0.0
        total_score += admin_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="admin_delinquency",
                weight=round(admin_weight, 1),
                awarded=admin_awarded,
                triggered=delinq_ok,
                evidence=delinq_reason,
                primary_source=delinq_source,
            )
        )
        if delinq_source and delinq_ok:
            primary_sources.append(delinq_source)

        # B2B Sector Fit
        b2b_weight = norm_weights.get("b2b_sector_fit", 0.0)
        b2b_awarded = round(b2b_weight, 1) if b2b_ok else 0.0
        total_score += b2b_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="b2b_sector_fit",
                weight=round(b2b_weight, 1),
                awarded=b2b_awarded,
                triggered=b2b_ok,
                evidence=b2b_reason,
                primary_source=b2b_source,
            )
        )
        if b2b_source and b2b_ok:
            primary_sources.append(b2b_source)

        final_score = min(100.0, round(total_score, 1))
        passed_gate = final_score >= self.min_gate_threshold

        return ScreeningResult(
            crn=crn,
            company_name=company_name,
            sic_codes=sic_codes,
            is_b2b_eligible=b2b_ok,
            provisional_score=final_score,
            passed_gate=passed_gate,
            knockout_applied=False,
            knockout_reason=None,
            audit_trail=audit_trail,
            primary_sources=primary_sources,
        )
