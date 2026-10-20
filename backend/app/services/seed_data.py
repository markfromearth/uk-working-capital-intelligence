from datetime import datetime, timezone, timedelta
from typing import List
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.models.company import CompanyRegistry, CompanyMatchReview
from backend.app.models.signal import NormalizedSignal
from backend.app.services.feature_store import FeatureStoreService
from backend.app.services.scoring_engine import LiquidityScoringEngine
from backend.app.services.llm_explainer import LLMExplanationService

def seed_demonstration_data(db: Session) -> List[CompanyRegistry]:
    """
    Populates the database with realistic, clearly labelled synthetic demonstration companies
    and multi-source signals.
    """
    now = datetime.now(timezone.utc)

    # Check if already seeded
    existing = db.query(CompanyRegistry).filter(CompanyRegistry.is_synthetic == 1).first()
    if existing:
        logger.info("Synthetic demonstration data already present in database.")
        return db.query(CompanyRegistry).filter(CompanyRegistry.is_synthetic == 1).all()

    companies_data = [
        {
            "crn": "08931201",
            "company_name": "Apex Precision Engineering Ltd",
            "company_status": "active",
            "company_type": "ltd",
            "registered_office_address": "Unit 4, Gateway Industrial Estate, Birmingham, B6 7EU",
            "sic_codes": "25620, 28290",
            "website_domain": "apex-precision-eng.co.uk",
            "source_reference": "DEMO-FIXTURE-01",
            "is_synthetic": 1,
            "scenario": "high_strain"  # Satisfied debenture + DBT 32 days + 38% hiring surge
        },
        {
            "crn": "SC123456",
            "company_name": "Caledonian Logistics Solutions Ltd",
            "company_status": "active",
            "company_type": "ltd",
            "registered_office_address": "88 Queensferry Road, Edinburgh, EH4 2BW",
            "sic_codes": "49410, 52103",
            "website_domain": "caledonian-logistics.co.uk",
            "source_reference": "DEMO-FIXTURE-02",
            "is_synthetic": 1,
            "scenario": "emerging_strain"  # Moderate DBT increase (18 days), no charge changes
        },
        {
            "crn": "11234567",
            "company_name": "Meridian Health Solutions Ltd",
            "company_status": "active",
            "company_type": "ltd",
            "registered_office_address": "33 Kingsway, London, WC2B 6UF",
            "sic_codes": "86900, 72110",
            "website_domain": "meridian-health.co.uk",
            "source_reference": "DEMO-FIXTURE-03",
            "is_synthetic": 1,
            "scenario": "low_strain"  # Zero DBT, stable headcount, no charge activity
        },
        {
            "crn": "04321987",
            "company_name": "Vanguard Precision Components PLC",
            "company_status": "active",
            "company_type": "plc",
            "registered_office_address": "Vanguard Works, Sheffield, S9 1XU",
            "sic_codes": "24100, 24520",
            "website_domain": "vanguard-precision.co.uk",
            "source_reference": "DEMO-FIXTURE-04",
            "is_synthetic": 1,
            "scenario": "elevated_strain"  # Partial charge satisfaction + credit rating downgrade
        }
    ]

    created_companies = []
    for cdata in companies_data:
        scenario = cdata.pop("scenario")
        crn = cdata["crn"]
        company = db.query(CompanyRegistry).filter(CompanyRegistry.crn == crn).first()
        if not company:
            company = CompanyRegistry(**cdata)
            db.add(company)
            db.commit()
            db.refresh(company)
        created_companies.append(company)

        # Check if signals already seeded for this company
        if db.query(NormalizedSignal).filter(NormalizedSignal.crn == crn).first():
            continue

        signals = []

        if scenario == "high_strain":
            # 1. Companies House MR04 Charge Satisfaction
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="charge_satisfaction_mr04",
                source_provider="companies_house",
                original_event_ref=f"chg-sat-{crn}-01",
                observation_timestamp=now - timedelta(days=14),
                value_numeric=1.0,
                value_text="Statement of satisfaction in full of debenture charge (MR04)",
                value_units="scope_weight",
                direction="strain_increasing",
                confidence_score=0.98,
                evidence_reference=f"https://find-and-update.company-information.service.gov.uk/company/{crn}/charges",
                metadata_json='{"scope": "full", "persons_entitled": ["National Westminster Bank PLC"]}'
            ))
            # 2. Creditsafe DBT surge
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="dbt_increase",
                source_provider="creditsafe",
                original_event_ref=f"cs-dbt-{crn}-01",
                observation_timestamp=now - timedelta(days=8),
                value_numeric=32.4,
                value_text="Supplier Days Beyond Terms increased to 32.4 days (+14.2 days overdue)",
                value_units="days",
                direction="strain_increasing",
                confidence_score=0.95,
                evidence_reference=f"Creditsafe Trade Payment Ledger #{crn}",
                metadata_json='{"dbt_days": 32.4, "dbt_change_days": 14.2}'
            ))
            # 3. Credit score drop
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="credit_score_drop",
                source_provider="creditsafe",
                original_event_ref=f"cs-score-{crn}-01",
                observation_timestamp=now - timedelta(days=22),
                value_numeric=24.0,
                value_text="Commercial credit score dropped 24 points to 38 (High Risk)",
                value_units="score_points",
                direction="strain_increasing",
                confidence_score=0.92,
                evidence_reference=f"Creditsafe Rating History #{crn}",
                metadata_json='{"current_score": 38, "score_drop": 24.0}'
            ))
            # 4. Workforce Headcount Surge
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="headcount_surge",
                source_provider="workforce_intelligence",
                original_event_ref=f"wf-surge-{crn}-01",
                observation_timestamp=now - timedelta(days=5),
                value_numeric=38.5,
                value_text="Headcount expanded 38.5% over 90 days (52 -> 72 employees)",
                value_units="%",
                direction="strain_increasing",
                confidence_score=0.90,
                evidence_reference=f"Proxycurl / Workforce Observation Series ({crn})",
                metadata_json='{"current_headcount": 72, "growth_90d_pct": 38.5, "growth_30d_pct": 12.0, "acceleration_pct": 15.2, "observation_count": 5}'
            ))

        elif scenario == "emerging_strain":
            # DBT deterioration only
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="dbt_increase",
                source_provider="creditsafe",
                original_event_ref=f"cs-dbt-{crn}-02",
                observation_timestamp=now - timedelta(days=18),
                value_numeric=18.0,
                value_text="Days Beyond Terms rose to 18.0 days",
                value_units="days",
                direction="strain_increasing",
                confidence_score=0.88,
                evidence_reference=f"Creditsafe Ledger #{crn}",
                metadata_json='{"dbt_days": 18.0, "dbt_change_days": 6.5}'
            ))

        elif scenario == "elevated_strain":
            # Partial satisfaction + credit limit reduction
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="charge_satisfaction_mr04",
                source_provider="companies_house",
                original_event_ref=f"chg-sat-{crn}-03",
                observation_timestamp=now - timedelta(days=35),
                value_numeric=0.5,
                value_text="Statement of partial satisfaction of charge (MR04)",
                value_units="scope_weight",
                direction="strain_increasing",
                confidence_score=0.90,
                evidence_reference=f"Companies House Charge Register {crn}",
                metadata_json='{"scope": "partial", "persons_entitled": ["HSBC UK Bank PLC"]}'
            ))
            signals.append(NormalizedSignal(
                crn=crn,
                signal_type="credit_limit_reduction",
                source_provider="experian",
                original_event_ref=f"exp-limit-{crn}-01",
                observation_timestamp=now - timedelta(days=20),
                value_numeric=30.0,
                value_text="Commercial credit limit curtailed by 30%",
                value_units="%",
                direction="strain_increasing",
                confidence_score=0.87,
                evidence_reference=f"Experian Risk Notice {crn}",
                metadata_json='{"curtailment_pct": 30.0}'
            ))

        for s in signals:
            db.add(s)
        db.commit()

        # Compute initial feature snapshot, score, and explanation
        fs_service = FeatureStoreService(db)
        engine = LiquidityScoringEngine(db)
        explainer = LLMExplanationService(db)

        snapshot = fs_service.compute_feature_snapshot(crn, as_of_timestamp=now)
        score_rec = engine.calculate_score(snapshot, as_of_timestamp=now)
        explanation = explainer.generate_explanation(crn, score_rec, snapshot)
        
        # Save generated summary to score record
        score_rec.ai_summary = explanation.reasoning_summary
        score_rec.ai_model_version = explanation.model_version
        score_rec.ai_generated_at = explanation.generated_at
        db.commit()

    # Seed an ambiguous entity match in the review queue to test manual resolution workflow
    existing_match = db.query(CompanyMatchReview).first()
    if not existing_match:
        review_match = CompanyMatchReview(
            source_identifier="APEX PRECISION ENGINERING (UK)",
            candidate_crn="08931201",
            candidate_name="Apex Precision Engineering Ltd",
            source_provider="commercial_credit_feed",
            match_score=0.82,
            resolution_method="probabilistic_fuzzy_needs_review",
            status="pending",
            raw_payload_summary="Input: 'APEX PRECISION ENGINERING (UK)', Postcode: 'B6 7EU'. Candidate: 'Apex Precision Engineering Ltd', CRN: 08931201",
            created_at=now
        )
        db.add(review_match)
        db.commit()

    logger.info(f"Seeded {len(created_companies)} synthetic companies with features, scores, and review fixtures.")
    return created_companies
