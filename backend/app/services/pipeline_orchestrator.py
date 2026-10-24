"""
End-to-End Pipeline Orchestrator for CH-Only Invoice Finance Warden.
Coordinates:
- Stage 1: Structured Screening & Provisional Scoring (The Gatekeeper)
- Stage 2: Quant Accounts Extraction & Trapped Cash Sizing
- Stage 3: Post-Gate Intelligence Refinement Agent & Primary Source Attribution
"""

import asyncio
import json
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
from backend.app.services.indicator_engine import summarise_charges, ELIGIBLE_B2B_PREFIXES
from backend.app.services.product_matcher import match_products, is_surfaced, triggered_indicators


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
        discovery_source: Optional[str] = None,
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

        # Collect public procurement & gazette statutory signals
        from backend.app.providers.registry import provider_registry
        signals: List[Dict[str, Any]] = []
        cf_adapter = provider_registry.get_adapter("contracts_finder")
        if cf_adapter:
            signals.extend(cf_adapter.fetch_company_signals(norm_crn))
        gz_adapter = provider_registry.get_adapter("the_gazette")
        if gz_adapter:
            signals.extend(gz_adapter.fetch_company_signals(norm_crn))

        # Configure dynamic scorer
        scorer = ProvisionalScorer(
            weights=weights,
            toggles=toggles,
            archetype=archetype,
            min_gate_threshold=min_gate_threshold,
        )
        screening_res = scorer.score_company(norm_crn, profile, charges, filings, signals=signals)
        self._screening_store[norm_crn] = screening_res
        products = match_products(screening_res)
        self._persist_screening(screening_res, profile, charges, products, discovery_source)

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
                "surfaced": is_surfaced(screening_res),
                "products": products,
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
                        if not balance_sheet.period_end_date and acc_meta.get("date"):
                            balance_sheet.period_end_date = acc_meta.get("date")
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
        self._persist_dossier(norm_crn, dossier)

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
            "surfaced": is_surfaced(screening_res),
            "products": products,
        }

    def get_prospects(self) -> List[TrappedCashDossier]:
        """Return only genuine businesses that have presented active indicators from Companies House."""
        prospects = [
            d for d in self._dossier_store.values()
            if d.provisional_score > 0 and len(d.primary_sources) > 0
        ]
        return sorted(prospects, key=lambda d: d.indicative_facility_80pct, reverse=True)

    def get_dossier(self, crn: str) -> Optional[TrappedCashDossier]:
        """Retrieve stored dossier for a company (memory, then persistent store)."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        key = norm_crn if valid and norm_crn else crn
        if key in self._dossier_store:
            return self._dossier_store[key]
        rec = self._load_record(key)
        if rec is not None and rec.dossier_json:
            try:
                dossier = TrappedCashDossier.model_validate_json(rec.dossier_json)
                self._dossier_store[key] = dossier
                return dossier
            except Exception as e:
                logger.warning(f"Could not hydrate dossier for {key}: {e}")
        return None

    def get_screening(self, crn: str) -> Optional[ScreeningResult]:
        """Retrieve stored Stage 1 screening result (memory, then persistent store)."""
        norm_crn, valid, _ = normalize_uk_crn(crn)
        key = norm_crn if valid and norm_crn else crn
        if key in self._screening_store:
            return self._screening_store[key]
        rec = self._load_record(key)
        if rec is not None and rec.screening_json:
            try:
                screening = ScreeningResult.model_validate_json(rec.screening_json)
                self._screening_store[key] = screening
                return screening
            except Exception as e:
                logger.warning(f"Could not hydrate screening for {key}: {e}")
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _load_record(crn: str):
        from backend.app.database import SessionLocal
        from backend.app.models.warden import WardenScreeningRecord

        db = SessionLocal()
        try:
            return db.query(WardenScreeningRecord).filter(WardenScreeningRecord.crn == crn).first()
        except Exception:
            return None
        finally:
            db.close()

    def _persist_screening(
        self,
        screening: ScreeningResult,
        profile: Dict[str, Any],
        charges: List[Dict[str, Any]],
        products: List[Dict[str, Any]],
        discovery_source: Optional[str] = None,
    ) -> None:
        from backend.app.database import SessionLocal
        from backend.app.models.warden import WardenScreeningRecord
        from backend.app.models.company import CompanyRegistry

        signals = triggered_indicators(screening)
        surfaced = is_surfaced(screening)
        db = SessionLocal()
        try:
            rec = db.query(WardenScreeningRecord).filter(WardenScreeningRecord.crn == screening.crn).first()
            if rec is None:
                rec = WardenScreeningRecord(crn=screening.crn)
                db.add(rec)
            rec.company_name = screening.company_name
            rec.company_status = profile.get("company_status")
            rec.sic_codes = ",".join(screening.sic_codes or [])
            rec.is_b2b = int(bool(screening.is_b2b_eligible))
            rec.knockout_applied = int(bool(screening.knockout_applied))
            rec.knockout_reason = screening.knockout_reason
            rec.provisional_score = screening.provisional_score
            rec.surfaced = int(surfaced)
            rec.indicator_count = len(signals)
            rec.triggered_indicators = json.dumps(signals)
            rec.matched_products = json.dumps(products)
            rec.product_codes = "," + ",".join(p["code"] for p in products) + "," if products else ""
            rec.charges_summary = json.dumps(summarise_charges(charges))
            rec.screening_json = screening.model_dump_json()
            if discovery_source:
                rec.discovery_source = discovery_source

            # Keep the company registry in sync for genuine screened entities (surfaced or explicitly screened)
            comp = db.query(CompanyRegistry).filter(CompanyRegistry.crn == screening.crn).first()
            if comp is None:
                comp = CompanyRegistry(crn=screening.crn, company_name=screening.company_name, is_synthetic=0)
                db.add(comp)
            comp.company_name = screening.company_name
            comp.company_status = profile.get("company_status", comp.company_status)
            comp.company_type = profile.get("type", comp.company_type)
            comp.sic_codes = ",".join(screening.sic_codes or [])
            comp.source_reference = "COMPANIES_HOUSE_API"
            comp.is_synthetic = 0
            db.commit()
        except Exception as e:
            logger.warning(f"Could not persist screening for {screening.crn}: {e}")
            db.rollback()
        finally:
            db.close()

    def _persist_dossier(self, crn: str, dossier: TrappedCashDossier) -> None:
        from backend.app.database import SessionLocal
        from backend.app.models.warden import WardenScreeningRecord

        db = SessionLocal()
        try:
            rec = db.query(WardenScreeningRecord).filter(WardenScreeningRecord.crn == crn).first()
            if rec is None:
                return
            rec.refined_score = dossier.refined_score
            rec.dossier_json = dossier.model_dump_json()
            if dossier.eligible_book_debts > 0:
                rec.eligible_book_debts = dossier.eligible_book_debts
                rec.indicative_facility = dossier.indicative_facility_80pct
                cat = dossier.balance_sheet.current_assets_total if dossier.balance_sheet else 0
                if cat and cat > 0:
                    rec.debtors_to_assets_ratio = int(round(dossier.eligible_book_debts / cat * 100))
            db.commit()
        except Exception as e:
            logger.warning(f"Could not persist dossier for {crn}: {e}")
            db.rollback()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Discovery: genuine Companies House advanced search -> screen -> surface
    # ------------------------------------------------------------------
    async def discover(
        self,
        sic_codes: List[str],
        size: int = 20,
        start_index: int = 0,
        incorporated_from: Optional[str] = None,
        incorporated_to: Optional[str] = None,
        concurrency: int = 3,
    ) -> Dict[str, Any]:
        """Find active B2B companies on Companies House and screen each one (Stage 1)."""
        search = await self.ch_client.advanced_search(
            sic_codes=sic_codes,
            size=size,
            start_index=start_index,
            incorporated_from=incorporated_from,
            incorporated_to=incorporated_to,
        )
        items = search.get("items", [])
        sem = asyncio.Semaphore(max(1, concurrency))
        source_tag = f"CH_ADVANCED_SEARCH:{','.join(sic_codes)[:80]}"

        async def _screen(item: Dict[str, Any]) -> Dict[str, Any]:
            crn = item.get("company_number")
            async with sem:
                try:
                    # Stage 1 only (gate = 101 so Stage 2 document downloads are deferred to profile view)
                    res = await self.process_company(crn, min_gate_threshold=101.0, discovery_source=source_tag)
                    scr = res.get("screening")
                    return {
                        "crn": crn,
                        "company_name": item.get("company_name"),
                        "surfaced": bool(res.get("surfaced")),
                        "indicators": triggered_indicators(scr) if scr else [],
                        "products": [p["code"] for p in res.get("products", [])],
                    }
                except Exception as e:
                    logger.warning(f"Discovery screen failed for {crn}: {e}")
                    return {"crn": crn, "company_name": item.get("company_name"), "surfaced": False, "error": str(e)}

        results = await asyncio.gather(*[_screen(i) for i in items if i.get("company_number")])
        return {
            "sic_codes": sic_codes,
            "start_index": start_index,
            "next_start_index": start_index + len(items),
            "total_hits": search.get("hits", 0),
            "screened": len(results),
            "surfaced": sum(1 for r in results if r.get("surfaced")),
            "results": results,
        }


# Global singleton instance for application API use
default_pipeline_orchestrator = WardenPipelineOrchestrator()
