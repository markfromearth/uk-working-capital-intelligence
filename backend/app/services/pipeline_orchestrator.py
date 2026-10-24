"""
End-to-End Pipeline Orchestrator for CH-Only Invoice Finance Warden.
Coordinates:
- Stage 1: Structured Screening & Provisional Scoring (The Gatekeeper)
- Stage 2: Quant Accounts Extraction & Trapped Cash Sizing
- Stage 3: Post-Gate Intelligence Refinement Agent & Primary Source Attribution
"""

from typing import Any, Dict, List, Optional
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.schemas.warden import (
    BalanceSheetData,
    LendingArchetype,
    PrimarySourceReference,
    ScreeningResult,
    TrappedCashDossier,
)
from backend.app.providers.companies_house_ch import CompaniesHouseCHClient
from backend.app.services.provisional_scorer import ProvisionalScorer
from backend.app.services.accounts_fetcher import AccountsFetcher
from backend.app.services.ixbrl_parser import IXBRLParser
from backend.app.services.pdf_vision_extractor import PDFVisionExtractor
from backend.app.services.quant_math import QuantMathEngine
from backend.app.services.refinement_agent import IntelligenceRefinementAgent


class WardenPipelineOrchestrator:
    """Orchestrates Stage 1 screening, Stage 2 financial sizing, and Stage 3 intelligence refinement."""

    def __init__(
        self,
        ch_client: Optional[CompaniesHouseCHClient] = None,
        scorer: Optional[ProvisionalScorer] = None,
        fetcher: Optional[AccountsFetcher] = None,
    ):
        self.ch_client = ch_client or CompaniesHouseCHClient()
        self.scorer = scorer or ProvisionalScorer()
        self.fetcher = fetcher or AccountsFetcher()
        self._dossier_store: Dict[str, TrappedCashDossier] = {}
        self._screening_store: Dict[str, ScreeningResult] = {}

    async def process_company(
        self,
        crn: str,
        weights: Optional[Dict[str, float]] = None,
        toggles: Optional[Dict[str, bool]] = None,
        archetype: LendingArchetype = LendingArchetype.PRIME_INVOICE_DISCOUNTING,
        min_gate_threshold: float = 65.0,
    ) -> Dict[str, Any]:
        """
        Execute end-to-end evaluation pipeline for a company.
        Bypasses Stage 2 document downloads if company fails the Stage 1 provisional score gate.
        """
        norm_crn, valid, err = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return {
                "status": "INVALID_CRN",
                "message": f"CRN normalization failed: {err}",
                "screening": None,
                "dossier": None,
            }

        # --- STAGE 1: Structured Screening & Provisional Scoring ---
        profile = await self.ch_client.fetch_profile(norm_crn)
        if not profile:
            return {
                "status": "NOT_FOUND",
                "message": f"Company not found in Companies House: {norm_crn}",
                "screening": None,
                "dossier": None,
            }

        charges = await self.ch_client.fetch_charges(norm_crn)
        filings = await self.ch_client.fetch_filing_history(norm_crn)
        officers = await self.ch_client.fetch_officers(norm_crn)

        # Configure dynamic scorer
        scorer = ProvisionalScorer(
            weights=weights,
            toggles=toggles,
            archetype=archetype,
            min_gate_threshold=min_gate_threshold,
        )
        screening_res = scorer.score_company(norm_crn, profile, charges, filings)
        self._screening_store[norm_crn] = screening_res

        # Gate Check: Stop here if below qualification gate or knocked out
        if not screening_res.passed_gate:
            logger.info(
                f"Warden gate barrier: {norm_crn} scored {screening_res.provisional_score} "
                f"(min {min_gate_threshold}). Stage 2 and Stage 3 bypassed."
            )
            return {
                "status": "BELOW_GATE",
                "message": "Company did not meet provisional score qualification threshold",
                "screening": screening_res,
                "dossier": None,
            }

        # --- STAGE 2: Quant Accounts Extraction & Trapped Cash Sizing ---
        logger.info(f"Warden gate passed for {norm_crn} ({screening_res.provisional_score} pts). Proceeding to Stage 2.")
        acc_meta = self.fetcher.extract_latest_accounts_metadata(filings)
        balance_sheet = None

        if acc_meta:
            doc_url = acc_meta.get("links", {}).get("document_metadata")
            if doc_url:
                content_bytes, content_type = await self.fetcher.download_document(doc_url)
                if content_bytes:
                    c_lower = (content_type or "").lower()
                    if "xhtml" in c_lower or "xml" in c_lower or "html" in c_lower:
                        balance_sheet = IXBRLParser.parse(content_bytes)
                    elif "pdf" in c_lower:
                        pruned_bytes, _ = PDFVisionExtractor.prune_to_balance_sheet(content_bytes)
                        if pruned_bytes:
                            balance_sheet = PDFVisionExtractor.extract_from_pruned_pdf(pruned_bytes)

                    # Attach document primary source reference
                    if balance_sheet:
                        balance_sheet.primary_source = PrimarySourceReference(
                            source_type="ACCOUNTS_DOCUMENT",
                            source_id=doc_url.rstrip("/").split("/")[-1],
                            title="Statutory Accounts Annual Filing Document",
                            uri=doc_url,
                            retrieval_method="DOCUMENT_API",
                            published_date=acc_meta.get("date"),
                            excerpt=f"Extracted net current assets: £{balance_sheet.net_current_assets:,.0f} ({balance_sheet.accounting_framework})",
                        )

        # If statutory accounts document was unobserved or unavailable from CH
        if not balance_sheet:
            balance_sheet = BalanceSheetData(
                extraction_path="UNOBSERVED_CH_ACCOUNTS",
                accounting_framework="UNOBSERVED",
                current_assets_total=0.0,
                debtors=None,
                cash_at_bank=None,
                creditors_due_within_one_year=0.0,
                net_current_assets=0.0,
                is_arithmetic_valid=True,
                primary_source=PrimarySourceReference(
                    source_type="ACCOUNTS_DOCUMENT",
                    source_id=f"UNOBSERVED_{norm_crn}",
                    title="Accounts Balance Sheet (Unobserved on Companies House)",
                    uri=f"https://find-and-update.company-information.service.gov.uk/company/{norm_crn}/filing-history",
                    retrieval_method="REST_API",
                    excerpt="Accounts filing not observed or pending digitisation on Companies House.",
                ),
            )

        # --- STAGE 3: Post-Gate Handoff to Intelligence Refinement Agent ---
        refined_score, adjustments, all_primary_sources = IntelligenceRefinementAgent.refine_provisional_score(
            screening=screening_res,
            balance_sheet=balance_sheet,
            profile=profile,
            charges=charges,
            filings=filings,
            officers=officers,
        )

        # Size facility and construct outreach copy
        dossier = QuantMathEngine.size_facility(
            screening=screening_res,
            balance_sheet=balance_sheet,
            refined_score=refined_score,
            refinements=adjustments,
            primary_sources=all_primary_sources,
        )
        self._dossier_store[norm_crn] = dossier

        # Persist genuine Companies House prospect to database for platform-wide visibility
        from backend.app.database import SessionLocal
        from backend.app.models.company import CompanyRegistry
        from backend.app.models.score import LiquidityScoreRecord
        
        db = SessionLocal()
        try:
            existing = db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
            if not existing:
                existing = CompanyRegistry(
                    crn=norm_crn,
                    company_name=screening_res.company_name,
                    company_status=profile.get("company_status", "active"),
                    company_type=profile.get("type", "ltd"),
                    sic_codes=",".join(screening_res.sic_codes),
                    is_synthetic=0,
                    source_reference="COMPANIES_HOUSE_API",
                )
                db.add(existing)
            else:
                existing.company_name = screening_res.company_name
                existing.is_synthetic = 0
                existing.source_reference = "COMPANIES_HOUSE_API"

            score_band = "strong" if dossier.refined_score >= 70.0 else ("elevated" if dossier.refined_score >= 40.0 else "emerging")
            score_rec = LiquidityScoreRecord(
                crn=norm_crn,
                score=dossier.refined_score,
                score_band=score_band,
                evidence_coverage="strong",
                model_version="ch-warden-v1",
                financing_position_score=min(25.0, screening_res.provisional_score * 0.35),
                ai_summary=dossier.commercial_pitch,
            )
            db.add(score_rec)
            db.commit()
        except Exception as e:
            logger.warning(f"Could not persist company {norm_crn} to DB: {e}")
            db.rollback()
        finally:
            db.close()

        return {
            "status": "QUALIFIED",
            "message": "Prospect successfully qualified, refined, and borrowing base sized",
            "screening": screening_res,
            "dossier": dossier,
        }

    def get_prospects(self) -> List[TrappedCashDossier]:
        """Return only genuine businesses that have presented active indicators from Companies House."""
        prospects = [
            d for d in self._dossier_store.values()
            if d.provisional_score > 0 and len(d.primary_sources) > 0
        ]
        return sorted(prospects, key=lambda d: d.indicative_facility_80pct, reverse=True)

    def get_dossier(self, crn: str) -> Optional[TrappedCashDossier]:
        """Retrieve stored dossier for a company."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        return self._dossier_store.get(norm_crn if valid and norm_crn else crn)

    def get_screening(self, crn: str) -> Optional[ScreeningResult]:
        """Retrieve stored Stage 1 screening result and indicator audit trail for a company."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        return self._screening_store.get(norm_crn if valid and norm_crn else crn)


# Global singleton instance for application API use
default_pipeline_orchestrator = WardenPipelineOrchestrator()
