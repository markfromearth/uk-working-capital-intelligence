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
    evaluate_aa01_extension,
    evaluate_admin_delinquency,
    evaluate_asset_finance_charges,
    evaluate_contract_awards,
    evaluate_gazette_insolvency,
    evaluate_incumbent_lender,
    evaluate_recent_event_velocity,
    evaluate_refinance_window,
    evaluate_sh01_allotments,
    evaluate_tm01_cluster,
    evaluate_white_space,
    is_b2b_eligible,
)

DEFAULT_INDICATOR_WEIGHTS = {
    "white_space": 30.0,
    "refinance_window": 20.0,
    "recent_event_velocity": 15.0,     # Mitigates statutory reporting latency
    "b2b_sector_fit": 15.0,
    "sh01_capital_allotment": 10.0,    # Intra-year equity injection
    "contract_award_growth": 10.0,     # Procurement win working capital lag
    "tm01_cluster": 5.0,
    "admin_delinquency": 5.0,
}

DEFAULT_TOGGLES = {
    "white_space": True,
    "refinance_window": True,
    "recent_event_velocity": True,
    "b2b_sector_fit": True,
    "sh01_capital_allotment": True,
    "contract_award_growth": True,
    "tm01_cluster": True,
    "admin_delinquency": True,
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
        signals: Optional[List[Dict[str, Any]]] = None,
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
        vel_ok, vel_factor, vel_reason, vel_source = evaluate_recent_event_velocity(charges, filings, as_of=now, crn=crn)
        sh01_ok, sh01_count, sh01_reason, sh01_source = evaluate_sh01_allotments(filings, as_of=now, crn=crn)
        aa01_ok, aa01_reason, aa01_source = evaluate_aa01_extension(filings, as_of=now, crn=crn)
        tm01_ok, tm01_count, tm01_reason, tm01_source = evaluate_tm01_cluster(filings, as_of=now, crn=crn)
        delinq_ok, gazette_or_insolvent, delinq_reason, delinq_source = evaluate_admin_delinquency(profile, filings, crn=crn)
        gz_ok, gz_reason, gz_source = evaluate_gazette_insolvency(signals or [], as_of=now, crn=crn)
        ca_ok, ca_need, ca_reason, ca_source = evaluate_contract_awards(signals or [], as_of=now, crn=crn)
        incumbent_ok, incumbent_reason, _incumbent_cls, incumbent_source = evaluate_incumbent_lender(charges, as_of=now, crn=crn)
        asset_ok, _asset_count, asset_reason, asset_source = evaluate_asset_finance_charges(charges, crn=crn)

        # 3. Check Hard Knockouts
        if self.archetype == LendingArchetype.PRIME_INVOICE_DISCOUNTING and (gazette_or_insolvent or gz_ok):
            knockout_reason = gz_reason if gz_ok else delinq_reason
            knockout_source = gz_source if gz_ok else (delinq_source or PrimarySourceReference(
                source_type="COMPANIES_HOUSE_PROFILE",
                source_id="HARD_KNOCKOUT",
                title="Disqualifying Strike-off/Insolvency Status",
                uri=f"https://find-and-update.company-information.service.gov.uk/company/{crn}",
                excerpt=delinq_reason,
            ))
            audit = [
                IndicatorBreakdown(
                    indicator="hard_knockout",
                    weight=0.0,
                    awarded=0.0,
                    triggered=True,
                    evidence=f"Disqualified under Prime rules: {knockout_reason}",
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
                knockout_reason=knockout_reason,
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

        # Recent Statutory Event Velocity (Trailing 90 Days - Mitigates Balance Sheet Latency)
        vel_weight = norm_weights.get("recent_event_velocity", 0.0)
        vel_awarded = round(vel_weight * vel_factor, 1) if vel_ok else 0.0
        total_score += vel_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="recent_event_velocity",
                weight=round(vel_weight, 1),
                awarded=vel_awarded,
                triggered=vel_ok,
                evidence=vel_reason,
                primary_source=vel_source,
            )
        )
        if vel_source and vel_ok:
            primary_sources.append(vel_source)

        # Form SH01 Mid-Year Share Allotment (Capital Injection)
        sh01_weight = norm_weights.get("sh01_capital_allotment", 0.0)
        sh01_awarded = round(sh01_weight, 1) if sh01_ok else 0.0
        total_score += sh01_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="sh01_capital_allotment",
                weight=round(sh01_weight, 1),
                awarded=sh01_awarded,
                triggered=sh01_ok,
                evidence=sh01_reason,
                primary_source=sh01_source,
            )
        )
        if sh01_source and sh01_ok:
            primary_sources.append(sh01_source)

        # Public Procurement Contract Award (Working Capital Mobilization Lag)
        ca_weight = norm_weights.get("contract_award_growth", 0.0)
        ca_awarded = round(ca_weight, 1) if ca_ok else 0.0
        total_score += ca_awarded
        audit_trail.append(
            IndicatorBreakdown(
                indicator="contract_award_growth",
                weight=round(ca_weight, 1),
                awarded=ca_awarded,
                triggered=ca_ok,
                evidence=ca_reason,
                primary_source=ca_source,
            )
        )
        if ca_source and ca_ok:
            primary_sources.append(ca_source)

        # Form AA01 Accounting Reference Date Extension (Intra-Year Distress Early Warning)
        if aa01_ok:
            audit_trail.append(
                IndicatorBreakdown(
                    indicator="aa01_date_extension",
                    weight=0.0,
                    awarded=0.0,
                    triggered=True,
                    evidence=aa01_reason,
                    primary_source=aa01_source,
                )
            )
            if aa01_source:
                primary_sources.append(aa01_source)

        # Multi-product informational indicators (zero weight; drive product matching & surfacing)
        for _ind, _ok, _reason, _src in (
            ("incumbent_lender_facility", incumbent_ok, incumbent_reason, incumbent_source),
            ("asset_finance_footprint", asset_ok, asset_reason, asset_source),
        ):
            if _ok:
                audit_trail.append(
                    IndicatorBreakdown(
                        indicator=_ind,
                        weight=0.0,
                        awarded=0.0,
                        triggered=True,
                        evidence=_reason,
                        primary_source=_src,
                    )
                )
                if _src:
                    primary_sources.append(_src)

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
