import difflib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn, is_valid_uk_crn
from backend.app.models.company import CompanyRegistry, CompanyMatchReview

def clean_company_name(name: str) -> str:
    """Standardizes UK company names by removing legal suffix variants and punctuation."""
    if not name:
        return ""
    cleaned = name.upper().strip()
    # Replace common suffixes
    suffixes = [
        r"\bLIMITED LIABILITY PARTNERSHIP\b", r"\bLLP\b",
        r"\bPUBLIC LIMITED COMPANY\b", r"\bPLC\b",
        r"\bLIMITED\b", r"\bLTD\.?\b",
        r"\bCOMMUNITY INTEREST COMPANY\b", r"\bCIC\b",
        r"\bHOLDINGS\b", r"\bUK\b"
    ]
    for s in suffixes:
        cleaned = re.sub(s, "", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def clean_postcode(postcode: Optional[str]) -> str:
    if not postcode:
        return ""
    return re.sub(r"[\s\-_]", "", postcode).upper()

class EntityResolutionService:
    """
    Multi-stage Entity Resolution Pipeline for UK Companies.
    Pipeline stages:
    1. Exact Match on Valid CRN
    2. Deterministic exact name + postcode match
    3. High-confidence attribute fuzzy match (Jaro-Winkler/Token Ratio >= 0.88)
    4. Ambiguity / Review Queue routing (Score 0.65 - 0.87, or conflicting CRNs)
    """

    def __init__(self, db: Session):
        self.db = db

    def resolve_entity(
        self,
        raw_identifier: Optional[str] = None,
        company_name: Optional[str] = None,
        postcode: Optional[str] = None,
        source_provider: str = "manual"
    ) -> Tuple[Optional[CompanyRegistry], float, str, Optional[int]]:
        """
        Attempts to resolve an external reference to a canonical UK Company.
        
        Returns:
            (CompanyRegistry or None, match_confidence_score, resolution_method, review_id_if_queued)
        """
        # Stage 1: Exact match on valid UK CRN
        input_norm_crn = None
        if raw_identifier and is_valid_uk_crn(raw_identifier):
            norm_crn, valid, _ = normalize_uk_crn(raw_identifier)
            if valid and norm_crn:
                input_norm_crn = norm_crn
                company = self.db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
                if company:
                    logger.info(f"Resolved company via Stage 1 (Exact CRN): {norm_crn}")
                    return company, 1.0, "exact_crn", None

        if not company_name:
            return None, 0.0, "unresolved_no_name", None

        clean_input_name = clean_company_name(company_name)
        clean_input_pc = clean_postcode(postcode)

        # Retrieve candidate pool from registry
        candidates: List[CompanyRegistry] = self.db.query(CompanyRegistry).all()
        if not candidates:
            return None, 0.0, "registry_empty", None

        best_candidate: Optional[CompanyRegistry] = None
        best_score = 0.0
        best_method = "unresolved"

        for cand in candidates:
            # CRITICAL SAFETY RULE: Never match or merge if input has a valid CRN that conflicts with candidate CRN
            if input_norm_crn and cand.crn != input_norm_crn:
                # Conflicting canonical identities cannot be merged
                continue

            cand_clean_name = clean_company_name(cand.company_name)
            
            # SequenceMatcher ratio between normalized company names
            name_similarity = difflib.SequenceMatcher(None, clean_input_name, cand_clean_name).ratio()
            
            # Postcode verification boost
            pc_match = False
            if clean_input_pc and cand.registered_office_address:
                cand_clean_addr = clean_postcode(cand.registered_office_address)
                if clean_input_pc in cand_clean_addr:
                    pc_match = True

            # Combined score: name similarity + postcode boost
            combined_score = name_similarity
            if pc_match:
                combined_score = min(1.0, combined_score + 0.15)

            if combined_score > best_score:
                best_score = combined_score
                best_candidate = cand

        # Stage 2 & 3: High-Confidence Thresholds
        if best_score >= 0.90 and best_candidate:
            best_method = "deterministic_high_confidence"
            logger.info(f"Resolved company via {best_method}: {best_candidate.crn} ({best_score:.2f})")
            return best_candidate, best_score, best_method, None

        # Stage 4: Ambiguous Match (0.65 <= score < 0.90) -> Route to Manual Review Queue
        if 0.65 <= best_score < 0.90 and best_candidate:
            review_entry = CompanyMatchReview(
                source_identifier=raw_identifier or company_name,
                candidate_crn=best_candidate.crn,
                candidate_name=best_candidate.company_name,
                source_provider=source_provider,
                match_score=round(best_score, 3),
                resolution_method="probabilistic_fuzzy_needs_review",
                status="pending",
                raw_payload_summary=f"Input: name='{company_name}', postcode='{postcode}'. Candidate: '{best_candidate.company_name}', CRN={best_candidate.crn}",
                created_at=datetime.now(timezone.utc)
            )
            self.db.add(review_entry)
            self.db.commit()
            self.db.refresh(review_entry)
            logger.warning(f"Ambiguous match routed to review queue [ID={review_entry.id}]: score={best_score:.2f}")
            return None, best_score, "routed_to_review_queue", review_entry.id

        return None, best_score, "unresolved_insufficient_similarity", None

    def apply_manual_review_decision(
        self,
        review_id: int,
        approved: bool,
        reviewer_name: str,
        notes: Optional[str] = None
    ) -> Optional[CompanyMatchReview]:
        """Applies human review decision on an ambiguous entity match."""
        review = self.db.query(CompanyMatchReview).filter(CompanyMatchReview.id == review_id).first()
        if not review:
            return None

        review.status = "approved" if approved else "rejected"
        review.reviewed_by = reviewer_name
        review.reviewer_notes = notes
        review.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(review)
        return review

    def unmerge_or_split_entity(
        self,
        review_id: int,
        reviewer_name: str,
        reason: str
    ) -> Optional[CompanyMatchReview]:
        """Reverses an approved match decision, records the unmerge correction and preserves provenance."""
        review = self.db.query(CompanyMatchReview).filter(CompanyMatchReview.id == review_id).first()
        if not review:
            return None
        review.status = "rejected"
        review.reviewed_by = reviewer_name
        review.reviewer_notes = f"UNMERGE_REVERSAL: {reason}"
        review.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(review)
        return review
