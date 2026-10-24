from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.models.signal import NormalizedSignal
from backend.app.models.score import LiquidityScoreRecord, ScoreContributionRecord
from backend.app.models.feature import CompanyFeatureSnapshot
from backend.app.models.review import BrokerReviewAction
from backend.app.models.user import AuditLog
from backend.app.schemas.company import CompanyOut, MatchReviewDecision
from backend.app.services.resolution import EntityResolutionService
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.scoring_engine import LiquidityScoringEngine
from backend.app.services.llm_explainer import LLMExplanationService
from backend.app.services.pipeline_orchestrator import default_pipeline_orchestrator

router = APIRouter(prefix="/companies", tags=["Companies & Intelligence Profiles"])

@router.get("", response_model=List[CompanyOut])
def list_companies(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(CompanyRegistry).filter(CompanyRegistry.is_synthetic == 0)
    if status:
        query = query.filter(CompanyRegistry.company_status == status)
    if search:
        query = query.filter(CompanyRegistry.company_name.ilike(f"%{search}%") | CompanyRegistry.crn.ilike(f"%{search}%"))
    
    companies = query.offset(skip).limit(limit).all()
    results = []

    for c in companies:
        out = CompanyOut.model_validate(c)
        dossier = default_pipeline_orchestrator.get_dossier(c.crn)
        screening = default_pipeline_orchestrator.get_screening(c.crn)
        
        latest_score = db.query(LiquidityScoreRecord).filter(
            LiquidityScoreRecord.crn == c.crn
        ).order_by(LiquidityScoreRecord.scored_at.desc()).first()

        if dossier:
            out.liquidity_strain_score = dossier.refined_score
            out.score_band = "HOT / A-Tier" if dossier.refined_score >= 80 else ("WARM / B-Tier" if dossier.refined_score >= 65 else "NURTURE / C-Tier")
            out.estimated_facility_size = dossier.indicative_facility_80pct
            out.eligible_book_debts = dossier.eligible_book_debts
            if dossier.balance_sheet and dossier.balance_sheet.current_assets_total > 0:
                out.debtors_to_assets_ratio = int((dossier.eligible_book_debts / dossier.balance_sheet.current_assets_total) * 100)
            out.primary_signals_summary = dossier.commercial_pitch[:120] + "..." if dossier.commercial_pitch else "Verified statutory profile"
            out.working_capital_risk = "None / Mild"
        elif screening:
            out.liquidity_strain_score = screening.provisional_score
            out.score_band = "HOT / A-Tier" if screening.provisional_score >= 80 else ("WARM / B-Tier" if screening.provisional_score >= 65 else "NURTURE / C-Tier")
            out.working_capital_risk = screening.knockout_reason or ("None / Mild" if screening.passed_gate else "Below Gate")
        elif latest_score:
            out.liquidity_strain_score = latest_score.score
            out.score_band = latest_score.score_band
            out.primary_signals_summary = latest_score.ai_summary
            out.working_capital_risk = "None / Mild"
        else:
            out.liquidity_strain_score = 75.0
            out.score_band = "WARM / B-Tier"
            out.working_capital_risk = "None / Mild"

        results.append(out)

    return results

@router.get("/{crn}")
@router.get("/{crn}/profile")
async def get_company_intelligence_profile(crn: str, db: Session = Depends(get_db)):
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK Company Registration Number: {err}")

    company = db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
    if not company:
        # Auto-screen on-demand from Companies House
        try:
            await default_pipeline_orchestrator.process_company(norm_crn)
            company = db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
        except Exception as e:
            logger.warning(f"Auto-screening during profile lookup failed for {norm_crn}: {e}")

        if not company:
            # Query profile directly from Companies House client
            prof = await default_pipeline_orchestrator.ch_client.fetch_profile(norm_crn)
            if not prof:
                raise HTTPException(status_code=404, detail=f"Company with CRN {norm_crn} not found in Companies House")
            company = CompanyRegistry(
                crn=norm_crn,
                company_name=prof.get("company_name", f"Company {norm_crn}"),
                company_status=prof.get("company_status", "active"),
                company_type=prof.get("type", "ltd"),
                sic_codes=",".join(prof.get("sic_codes", [])),
                is_synthetic=0,
                source_reference="COMPANIES_HOUSE_API",
            )
            db.add(company)
            db.commit()
            db.refresh(company)

    # Get latest score and contributions
    latest_score = db.query(LiquidityScoreRecord).filter(
        LiquidityScoreRecord.crn == norm_crn
    ).order_by(LiquidityScoreRecord.scored_at.desc()).first()

    contributions = []
    if latest_score:
        contributions = db.query(ScoreContributionRecord).filter(
            ScoreContributionRecord.score_record_id == latest_score.id
        ).all()

    # Get latest feature snapshot
    latest_snapshot = db.query(CompanyFeatureSnapshot).filter(
        CompanyFeatureSnapshot.crn == norm_crn
    ).order_by(CompanyFeatureSnapshot.as_of_timestamp.desc()).first()

    # Get signals timeline
    signals = db.query(NormalizedSignal).filter(
        NormalizedSignal.crn == norm_crn
    ).order_by(NormalizedSignal.observation_timestamp.desc()).all()

    # Get broker reviews
    reviews = db.query(BrokerReviewAction).filter(
        BrokerReviewAction.crn == norm_crn
    ).order_by(BrokerReviewAction.created_at.desc()).all()

    # Generate or return structured AI explanation
    explainer = LLMExplanationService(db)
    if latest_score and latest_snapshot:
        explanation = explainer.generate_explanation(norm_crn, latest_score, latest_snapshot)
    else:
        explanation = None

    # Retrieve qualified Warden dossier or screening if present
    warden_dossier = default_pipeline_orchestrator.get_dossier(norm_crn)
    screening_result = default_pipeline_orchestrator.get_screening(norm_crn)

    if not warden_dossier and not screening_result:
        try:
            await default_pipeline_orchestrator.process_company(norm_crn)
            warden_dossier = default_pipeline_orchestrator.get_dossier(norm_crn)
            screening_result = default_pipeline_orchestrator.get_screening(norm_crn)
        except Exception as e:
            logger.warning(f"On-demand screen during profile fetch for {norm_crn}: {e}")

    officers = []
    pscs = []
    try:
        officers = await default_pipeline_orchestrator.ch_client.fetch_officers(norm_crn)
        pscs = await default_pipeline_orchestrator.ch_client.fetch_pscs(norm_crn)
    except Exception as e:
        logger.warning(f"Error fetching officers/PSCs for {norm_crn}: {e}")

    return {
        "company": {
            "crn": company.crn,
            "company_name": company.company_name,
            "company_status": company.company_status,
            "company_type": company.company_type,
            "incorporation_date": company.incorporation_date,
            "registered_office_address": company.registered_office_address,
            "sic_codes": company.sic_codes,
            "website_domain": company.website_domain,
            "source_reference": company.source_reference,
            "is_synthetic": bool(company.is_synthetic),
            "verified_at": company.verified_at
        },
        "warden_dossier": warden_dossier.model_dump() if warden_dossier else None,
        "screening_result": screening_result.model_dump() if screening_result else None,
        "officers": officers,
        "pscs": pscs,
        "score_summary": {
            "score": latest_score.score if latest_score else None,
            "score_band": latest_score.score_band if latest_score else "limited",
            "evidence_coverage": latest_score.evidence_coverage if latest_score else "limited",
            "model_version": latest_score.model_version if latest_score else "v1.0.0",
            "financing_position_score": latest_score.financing_position_score if latest_score else 0.0,
            "credit_payment_risk_score": latest_score.credit_payment_risk_score if latest_score else 0.0,
            "workforce_expansion_score": latest_score.workforce_expansion_score if latest_score else 0.0,
            "corroboration_score": latest_score.corroboration_score if latest_score else 0.0,
            "contradictory_penalty": latest_score.contradictory_penalty if latest_score else 0.0,
            "scored_at": latest_score.scored_at if latest_score else None,
            "contributions": [
                {
                    "pillar": c.pillar,
                    "feature_name": c.feature_name,
                    "feature_value": c.feature_value,
                    "points_awarded": c.points_awarded,
                    "max_pillar_points": c.max_pillar_points,
                    "explanation": c.explanation
                } for c in contributions
            ]
        },
        "feature_snapshot": {
            "charges_satisfied_90d": latest_snapshot.charges_satisfied_90d if latest_snapshot else 0,
            "age_latest_charge_satisfaction_days": latest_snapshot.age_latest_charge_satisfaction_days if latest_snapshot else None,
            "latest_charge_scope": latest_snapshot.latest_charge_scope if latest_snapshot else None,
            "dbt_current_days": latest_snapshot.dbt_current_days if latest_snapshot else None,
            "dbt_trend_90d": latest_snapshot.dbt_trend_90d if latest_snapshot else None,
            "credit_score_current": latest_snapshot.credit_score_current if latest_snapshot else None,
            "credit_score_drop_90d": latest_snapshot.credit_score_drop_90d if latest_snapshot else None,
            "dso_current_days": latest_snapshot.dso_current_days if latest_snapshot else None,
            "dso_data_available": bool(latest_snapshot.dso_data_available) if latest_snapshot else False,
            "dso_unavailable_reason": "No genuine receivables ledger or verified DSO filing observed in public or subscribed records. DBT measures supplier lateness, not collection time." if not (latest_snapshot and latest_snapshot.dso_data_available) else None,
            "headcount_current": latest_snapshot.headcount_current if latest_snapshot else None,
            "headcount_growth_90d_pct": latest_snapshot.headcount_growth_90d_pct if latest_snapshot else None,
            "headcount_acceleration_pct": latest_snapshot.headcount_acceleration_pct if latest_snapshot else None,
            "headcount_confidence_score": latest_snapshot.headcount_confidence_score if latest_snapshot else 0.0,
            "corroboration_signal_count": latest_snapshot.corroboration_signal_count if latest_snapshot else 0,
            "distinct_source_count": latest_snapshot.distinct_source_count if latest_snapshot else 0,
            "evidence_coverage": latest_snapshot.evidence_coverage if latest_snapshot else "limited"
        },
        "explanation": explanation.model_dump() if explanation else None,
        "signals_timeline": [
            {
                "signal_id": s.signal_id,
                "signal_type": s.signal_type,
                "source_provider": s.source_provider,
                "original_event_ref": s.original_event_ref,
                "observation_timestamp": s.observation_timestamp,
                "value_numeric": s.value_numeric,
                "value_text": s.value_text,
                "value_units": s.value_units,
                "direction": s.direction,
                "confidence_score": s.confidence_score,
                "evidence_reference": s.evidence_reference,
                "verification_status": s.verification_status,
                "metadata_json": s.metadata_json
            } for s in signals
        ],
        "broker_reviews": [
            {
                "id": r.id,
                "review_status": r.review_status,
                "assigned_broker_name": r.assigned_broker_name,
                "notes": r.notes,
                "verification_request": r.verification_request,
                "outcome": r.outcome,
                "created_at": r.created_at
            } for r in reviews
        ]
    }

@router.post("/{crn}/recompute-score")
def recompute_company_score(crn: str, db: Session = Depends(get_db)):
    norm_crn, valid, err = normalize_uk_crn(crn)
    if not valid or not norm_crn:
        raise HTTPException(status_code=400, detail=f"Invalid UK CRN: {err}")

    fs_service = FeatureStoreService(db)
    engine = LiquidityScoringEngine(db)
    explainer = LLMExplanationService(db)

    snapshot = fs_service.compute_feature_snapshot(norm_crn)
    score_rec = engine.calculate_score(snapshot, snapshot.as_of_timestamp)
    explanation = explainer.generate_explanation(norm_crn, score_rec, snapshot)

    score_rec.ai_summary = explanation.reasoning_summary
    score_rec.ai_model_version = explanation.model_version
    score_rec.ai_generated_at = explanation.generated_at
    db.commit()

    return {"message": "Score recomputed successfully", "score": score_rec.score, "score_band": score_rec.score_band}
