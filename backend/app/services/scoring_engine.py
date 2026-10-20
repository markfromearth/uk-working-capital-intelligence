import json
import math
from datetime import datetime, timezone
from typing import List, Tuple
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.score import LiquidityScoreRecord, ScoreContributionRecord

SCORING_MODEL_VERSION = "v1.0.0"

class LiquidityScoringEngine:
    """
    Transparent, explainable, versioned Liquidity Strain Scoring Engine.
    Produces a 0-100 score indicating the strength of evidence that a UK business
    may be approaching a working-capital funding requirement.
    """

    def __init__(self, db: Session):
        self.db = db

    def calculate_score(
        self,
        snapshot: CompanyFeatureSnapshot,
        as_of_timestamp: datetime
    ) -> LiquidityScoreRecord:
        contributions: List[ScoreContributionRecord] = []

        # --- Pillar 1: Financing-Position Changes (Max 25 pts) ---
        p1_score = 0.0
        p1_max = 25.0

        if (snapshot.charges_satisfied_90d or 0) > 0:
            scope = snapshot.latest_charge_scope or "full"
            age = snapshot.age_latest_charge_satisfaction_days or 30.0
            
            # Recency decay (half-life 60 days)
            recency_multiplier = math.exp(-0.693 * (age / 60.0))
            
            base_pts = 20.0 if scope == "full" else 10.0
            pts = round(base_pts * recency_multiplier, 1)
            p1_score += pts
            
            contributions.append(ScoreContributionRecord(
                pillar="financing_position",
                feature_name="charge_satisfaction_mr04",
                feature_value=age,
                points_awarded=pts,
                max_pillar_points=p1_max,
                explanation=f"{scope.capitalize()} charge satisfaction filed {age:.0f} days ago (recency factor {recency_multiplier:.2f})"
            ))

            # Additional points if multiple satisfactions occurred without new charge creations
            if (snapshot.charges_satisfied_180d or 0) >= 2 and (snapshot.charges_created_90d or 0) == 0:
                mult_pts = min(5.0, ((snapshot.charges_satisfied_180d or 0) - 1) * 2.5)
                p1_score += mult_pts
                contributions.append(ScoreContributionRecord(
                    pillar="financing_position",
                    feature_name="multiple_satisfactions_unrefinanced",
                    feature_value=float(snapshot.charges_satisfied_180d or 0),
                    points_awarded=mult_pts,
                    max_pillar_points=p1_max,
                    explanation=f"{snapshot.charges_satisfied_180d} charges satisfied without registered replacement debentures"
                ))

        p1_score = min(p1_score, p1_max)

        # --- Pillar 2: Commercial Credit & Payment Risk (Max 35 pts) ---
        p2_score = 0.0
        p2_max = 35.0

        # Days Beyond Terms (DBT)
        if snapshot.dbt_current_days is not None:
            dbt = snapshot.dbt_current_days
            if dbt >= 25.0:
                dbt_pts = 20.0
            elif dbt >= 15.0:
                dbt_pts = 12.0
            elif dbt >= 5.0:
                dbt_pts = 5.0
            else:
                dbt_pts = 0.0
                
            p2_score += dbt_pts
            contributions.append(ScoreContributionRecord(
                pillar="credit_payment_risk",
                feature_name="days_beyond_terms_current",
                feature_value=dbt,
                points_awarded=dbt_pts,
                max_pillar_points=p2_max,
                explanation=f"Supplier Days Beyond Terms is {dbt:.1f} days (benchmarked against agreed commercial terms)"
            ))

            # DBT trend
            if snapshot.dbt_trend_90d and snapshot.dbt_trend_90d > 3.0:
                trend_pts = min(5.0, snapshot.dbt_trend_90d * 0.8)
                p2_score += trend_pts
                contributions.append(ScoreContributionRecord(
                    pillar="credit_payment_risk",
                    feature_name="dbt_deterioration_trend",
                    feature_value=snapshot.dbt_trend_90d,
                    points_awarded=round(trend_pts, 1),
                    max_pillar_points=p2_max,
                    explanation=f"Supplier payment lateness increased by {snapshot.dbt_trend_90d:+.1f} days over past quarter"
                ))

        # Commercial Credit Rating Drop
        if snapshot.credit_score_drop_90d and snapshot.credit_score_drop_90d >= 10.0:
            drop_pts = min(10.0, snapshot.credit_score_drop_90d * 0.4)
            p2_score += drop_pts
            contributions.append(ScoreContributionRecord(
                pillar="credit_payment_risk",
                feature_name="credit_score_downgrade",
                feature_value=snapshot.credit_score_drop_90d,
                points_awarded=round(drop_pts, 1),
                max_pillar_points=p2_max,
                explanation=f"Commercial credit bureau score dropped by {snapshot.credit_score_drop_90d:.0f} points"
            ))

        p2_score = min(p2_score, p2_max)

        # --- Pillar 3: Workforce & Headcount Dynamics (Max 20 pts) ---
        p3_score = 0.0
        p3_max = 20.0

        if snapshot.headcount_growth_90d_pct and snapshot.headcount_growth_90d_pct >= 10.0:
            growth = snapshot.headcount_growth_90d_pct
            conf = snapshot.headcount_confidence_score or 0.8
            
            if growth >= 30.0:
                base_hc_pts = 15.0
            elif growth >= 20.0:
                base_hc_pts = 10.0
            else:
                base_hc_pts = 5.0

            weighted_hc_pts = round(base_hc_pts * conf, 1)
            p3_score += weighted_hc_pts
            contributions.append(ScoreContributionRecord(
                pillar="workforce_expansion",
                feature_name="headcount_90d_surge",
                feature_value=growth,
                points_awarded=weighted_hc_pts,
                max_pillar_points=p3_max,
                explanation=f"Workforce expanded by {growth:.1f}% in 90 days (confidence weighting {conf:.2f})"
            ))

            # Acceleration bonus
            if snapshot.headcount_acceleration_pct and snapshot.headcount_acceleration_pct > 10.0:
                acc_pts = 5.0 * conf
                p3_score += acc_pts
                contributions.append(ScoreContributionRecord(
                    pillar="workforce_expansion",
                    feature_name="hiring_acceleration",
                    feature_value=snapshot.headcount_acceleration_pct,
                    points_awarded=round(acc_pts, 1),
                    max_pillar_points=p3_max,
                    explanation=f"Hiring pace accelerating (+{snapshot.headcount_acceleration_pct:.1f}% annualized momentum)"
                ))

        p3_score = min(p3_score, p3_max)

        # --- Pillar 4: Corroboration & Trend Persistence (Max 20 pts) ---
        p4_score = 0.0
        p4_max = 20.0

        corrob_count = snapshot.corroboration_signal_count or 0
        source_count = snapshot.distinct_source_count or 0

        if source_count >= 3 and corrob_count >= 3:
            p4_score = 20.0
            exp = "Multi-source corroboration: signals confirmed across 3 independent provider registries"
        elif source_count >= 2 and corrob_count >= 2:
            p4_score = 12.0
            exp = "Dual-source corroboration: signals confirmed across 2 independent provider registries"
        elif corrob_count >= 1:
            p4_score = 4.0
            exp = "Single source observation: uncorroborated by independent datasets"
        else:
            p4_score = 0.0
            exp = "No active liquidity strain signals detected"

        contributions.append(ScoreContributionRecord(
            pillar="corroboration",
            feature_name="cross_source_corroboration",
            feature_value=float(corrob_count),
            points_awarded=p4_score,
            max_pillar_points=p4_max,
            explanation=exp
        ))

        # --- Contradictory Evidence Dampening ---
        penalty = 0.0
        if snapshot.contradictory_evidence_flags:
            try:
                flags = json.loads(snapshot.contradictory_evidence_flags)
                if flags:
                    penalty = min(20.0, len(flags) * 10.0)
                    contributions.append(ScoreContributionRecord(
                        pillar="mitigating_evidence",
                        feature_name="contradictory_mitigation",
                        feature_value=-penalty,
                        points_awarded=-penalty,
                        max_pillar_points=0.0,
                        explanation=f"Score dampened by {penalty:.0f} pts due to mitigating signals: {', '.join(flags)}"
                    ))
            except Exception:
                pass

        raw_unclamped = p1_score + p2_score + p3_score + p4_score - penalty
        final_score = max(0.0, min(100.0, raw_unclamped))

        # --- Minimum Evidence Quality Rule ---
        # A prospect cannot receive "strong" (>= 75) or "elevated" (>= 50) without at least 2 distinct signal sources
        if final_score >= 50.0 and source_count < 2:
            logger.info(f"Capping score for {snapshot.crn} at 49.0 due to lack of multi-source corroboration")
            final_score = min(final_score, 49.0)

        # Operational score band mapping
        if final_score >= 75.0:
            band = "strong"
        elif final_score >= 50.0:
            band = "elevated"
        elif final_score >= 25.0:
            band = "emerging"
        else:
            band = "limited"

        score_rec = LiquidityScoreRecord(
            crn=snapshot.crn,
            snapshot_id=snapshot.snapshot_id,
            score=round(final_score, 1),
            score_band=band,
            evidence_coverage=snapshot.evidence_coverage,
            model_version=SCORING_MODEL_VERSION,
            financing_position_score=round(p1_score, 1),
            credit_payment_risk_score=round(p2_score, 1),
            workforce_expansion_score=round(p3_score, 1),
            corroboration_score=round(p4_score, 1),
            decay_factor=1.0,
            contradictory_penalty=round(penalty, 1),
            raw_unclamped_score=round(raw_unclamped, 1),
            scored_at=as_of_timestamp
        )
        self.db.add(score_rec)
        self.db.commit()
        self.db.refresh(score_rec)

        # Attach contributions to score record
        for c in contributions:
            c.score_record_id = score_rec.id
            self.db.add(c)
        self.db.commit()

        return score_rec
