import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.core.logging import logger
from backend.app.models.company import CompanyRegistry
from backend.app.models.decision_maker import PersonIdentity, CompanyOfficer, ProfessionalRoleProfile
from backend.app.providers.registry import provider_registry
from backend.app.services.audit import log_audit_event

FINANCE_DECISION_MAKER_REGEX = re.compile(
    r"\b(finance\s+director|cfo|chief\s+financial\s+officer|financial\s+controller|"
    r"managing\s+director|md|chief\s+executive\s+officer|ceo|owner|proprietor|partner)\b",
    re.IGNORECASE
)

def parse_ownership_tiers(natures_of_control: List[str]) -> Tuple[Optional[float], Optional[float]]:
    """Extracts numeric ownership percentage bounds from Companies House PSC natures of control."""
    lower = None
    upper = None
    for item in natures_of_control:
        if "ownership-of-shares-25-to-50-percent" in item or "voting-rights-25-to-50-percent" in item:
            lower = max(lower or 0.0, 25.0)
            upper = max(upper or 0.0, 50.0)
        elif "ownership-of-shares-50-to-75-percent" in item or "voting-rights-50-to-75-percent" in item:
            lower = max(lower or 0.0, 50.0)
            upper = max(upper or 0.0, 75.0)
        elif "ownership-of-shares-75-to-100-percent" in item or "voting-rights-75-to-100-percent" in item:
            lower = max(lower or 0.0, 75.0)
            upper = 100.0
    return lower, upper

def normalize_person_name(name: str) -> str:
    """Normalizes person names for identity matching (removes titles, commas, excess spaces, lowercase)."""
    clean = re.sub(r"\b(mr|mrs|miss|ms|dr|sir|dame|prof|lord|lady)\b\.?", "", name, flags=re.IGNORECASE)
    parts = [p.strip() for p in re.split(r"[,/]+", clean) if p.strip()]
    normalized = " ".join(parts).lower()
    return re.sub(r"\s+", " ", normalized).strip()

class DecisionMakerDiscoveryService:
    """
    Retrieves and structures statutory officers and PSC registers from Companies House,
    distinguishes statutory directorship from operational responsibility and significant control,
    and maps candidate decision-makers.
    """

    def __init__(self, db: Session):
        self.db = db

    def discover_company_personnel(
        self,
        raw_crn: str,
        actor_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes discovery of statutory officers and PSCs for a company.
        Preserves historicalceased appointments and handles ceased control correctly.
        """
        norm_crn, valid, err = normalize_uk_crn(raw_crn)
        if not valid or not norm_crn:
            raise ValueError(f"Invalid UK CRN: {err}")

        company = self.db.query(CompanyRegistry).filter(CompanyRegistry.crn == norm_crn).first()
        if not company:
            raise ValueError(f"Company {norm_crn} not found in registry.")

        adapter = provider_registry.get_adapter("companies_house")
        raw_officers = adapter.fetch_company_officers(norm_crn)
        raw_psc = adapter.fetch_company_psc(norm_crn)

        officers_ingested = 0
        psc_ingested = 0
        decision_makers_mapped = 0

        # 1. Process Statutory Officers (Directors, Secretaries)
        for off in raw_officers:
            name = off.get("name", "Unknown Officer")
            role = off.get("officer_role", "director").lower()
            appointed_on_str = off.get("appointed_on")
            resigned_on_str = off.get("resigned_on")
            occupation = off.get("occupation") or role.replace("-", " ").title()
            dob = off.get("date_of_birth", {})
            dob_year = dob.get("year")
            dob_month = dob.get("month")

            appointed_on = datetime.strptime(appointed_on_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if appointed_on_str else None
            resigned_on = datetime.strptime(resigned_on_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if resigned_on_str else None
            is_active = (resigned_on is None)

            # Check existing officer record for idempotency
            existing = self.db.query(CompanyOfficer).filter(
                CompanyOfficer.crn == norm_crn,
                CompanyOfficer.officer_name == name,
                CompanyOfficer.officer_role == role
            ).first()

            if not existing:
                officer_rec = CompanyOfficer(
                    crn=norm_crn,
                    officer_name=name,
                    officer_role=role,
                    relationship_category="directorship" if "director" in role else "secretarial",
                    appointed_on=appointed_on,
                    resigned_on=resigned_on,
                    is_active=is_active,
                    occupation=occupation,
                    nationality=off.get("nationality"),
                    date_of_birth_month=dob_month,
                    date_of_birth_year=dob_year,
                    source_system="companies_house_officers",
                    source_reference=off.get("links", {}).get("officer", {}).get("appointments")
                )
                self.db.add(officer_rec)
                self.db.flush()
                officers_ingested += 1
            else:
                officer_rec = existing
                officer_rec.resigned_on = resigned_on
                officer_rec.is_active = is_active
                officer_rec.occupation = occupation

            # Link or create PersonIdentity
            person = self._find_or_create_person(
                full_name=name,
                birth_year=dob_year,
                birth_month=dob_month,
                nationality=off.get("nationality")
            )
            officer_rec.person_id = person.person_id

            # Map Professional Role & Decision-Maker Relevance
            if is_active:
                is_finance_dm = bool(FINANCE_DECISION_MAKER_REGEX.search(occupation) or FINANCE_DECISION_MAKER_REGEX.search(role))
                self._upsert_professional_role(
                    person=person,
                    crn=norm_crn,
                    role_title=occupation if occupation else role.replace("-", " ").title(),
                    is_finance_decision_maker=is_finance_dm,
                    is_current=True,
                    start_date=appointed_on,
                    platform="companies_house",
                    source_provider="companies_house",
                    evidence={"filing_source": "official_companies_house_register", "officer_role": role, "appointed_on": appointed_on_str}
                )
                if is_finance_dm:
                    decision_makers_mapped += 1

        # 2. Process Persons with Significant Control (PSC)
        for p in raw_psc:
            name = p.get("name", "Unknown PSC")
            kind = p.get("kind", "individual-person-with-significant-control")
            notified_on_str = p.get("notified_on")
            ceased_on_str = p.get("ceased_on")
            dob = p.get("date_of_birth", {})
            dob_year = dob.get("year")
            dob_month = dob.get("month")
            natures = p.get("natures_of_control", [])

            notified_on = datetime.strptime(notified_on_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if notified_on_str else None
            ceased_on = datetime.strptime(ceased_on_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if ceased_on_str else None
            is_active = (ceased_on is None)

            lower_pct, upper_pct = parse_ownership_tiers(natures)

            existing_psc = self.db.query(CompanyOfficer).filter(
                CompanyOfficer.crn == norm_crn,
                CompanyOfficer.officer_name == name,
                CompanyOfficer.officer_role == "psc"
            ).first()

            if not existing_psc:
                psc_rec = CompanyOfficer(
                    crn=norm_crn,
                    officer_name=name,
                    officer_role="psc",
                    relationship_category="ownership" if lower_pct and lower_pct >= 25.0 else "control",
                    notified_on=notified_on,
                    ceased_on=ceased_on,
                    is_active=is_active,
                    natures_of_control=json.dumps(natures),
                    ownership_percentage_lower=lower_pct,
                    ownership_percentage_upper=upper_pct,
                    nationality=p.get("nationality"),
                    date_of_birth_month=dob_month,
                    date_of_birth_year=dob_year,
                    source_system="companies_house_psc",
                    source_reference=p.get("links", {}).get("self")
                )
                self.db.add(psc_rec)
                self.db.flush()
                psc_ingested += 1
            else:
                psc_rec = existing_psc
                psc_rec.ceased_on = ceased_on
                psc_rec.is_active = is_active
                psc_rec.natures_of_control = json.dumps(natures)

            # Link or create PersonIdentity
            person = self._find_or_create_person(
                full_name=name,
                birth_year=dob_year,
                birth_month=dob_month,
                nationality=p.get("nationality")
            )
            psc_rec.person_id = person.person_id

            # Rule: Do not assume PSC is the finance decision-maker unless combined with operational directorship
            # An individual who is purely a PSC has an ownership profile, but is NOT marked as finance DM automatically
            if is_active:
                existing_role = self.db.query(ProfessionalRoleProfile).filter(
                    ProfessionalRoleProfile.person_id == person.person_id,
                    ProfessionalRoleProfile.crn == norm_crn
                ).first()

                # If no role profile exists, create an ownership role (is_finance_decision_maker=False by default)
                if not existing_role:
                    self._upsert_professional_role(
                        person=person,
                        crn=norm_crn,
                        role_title=f"Person with Significant Control ({int(lower_pct or 25)}-{int(upper_pct or 50)}% Ownership)",
                        is_finance_decision_maker=False,  # Strict rule: PSC alone is NOT assumed to be finance DM
                        is_current=True,
                        start_date=notified_on,
                        platform="companies_house",
                        source_provider="companies_house_psc",
                        evidence={"filing_source": "psc_register", "control_types": natures}
                    )

        self.db.commit()

        # Audit log
        log_audit_event(
            db=self.db,
            action="decision_makers_discovered",
            resource_type="company",
            resource_id=norm_crn,
            user_id=actor_user_id,
            details={
                "officers_ingested": officers_ingested,
                "psc_ingested": psc_ingested,
                "decision_makers_mapped": decision_makers_mapped
            }
        )

        return {
            "crn": norm_crn,
            "officers_found": len(raw_officers),
            "psc_found": len(raw_psc),
            "officers_ingested": officers_ingested,
            "psc_ingested": psc_ingested,
            "decision_makers_mapped": decision_makers_mapped
        }

    def _find_or_create_person(
        self,
        full_name: str,
        birth_year: Optional[int] = None,
        birth_month: Optional[int] = None,
        nationality: Optional[str] = None
    ) -> PersonIdentity:
        """Resolves or provisions a PersonIdentity record using normalized name and demographic corroboration."""
        norm_name = normalize_person_name(full_name)
        query = self.db.query(PersonIdentity).filter(PersonIdentity.normalized_name == norm_name)
        if birth_year:
            query = query.filter(PersonIdentity.birth_year == birth_year)

        person = query.first()
        if not person:
            person = PersonIdentity(
                full_name=full_name,
                normalized_name=norm_name,
                birth_year=birth_year,
                birth_month=birth_month,
                nationality=nationality
            )
            self.db.add(person)
            self.db.flush()
        return person

    def _upsert_professional_role(
        self,
        person: PersonIdentity,
        crn: str,
        role_title: str,
        is_finance_decision_maker: bool,
        is_current: bool,
        start_date: Optional[datetime],
        platform: str,
        source_provider: str,
        evidence: Dict[str, Any]
    ) -> ProfessionalRoleProfile:
        """Upserts a professional role profile linked to a person and canonical company."""
        std_role = "other"
        ltitle = role_title.lower()
        if "finance director" in ltitle or "cfo" in ltitle:
            std_role = "finance_director"
        elif "managing director" in ltitle or "ceo" in ltitle:
            std_role = "managing_director"
        elif "secretary" in ltitle:
            std_role = "secretary"
        elif "psc" in ltitle or "ownership" in ltitle:
            std_role = "owner"

        existing = self.db.query(ProfessionalRoleProfile).filter(
            ProfessionalRoleProfile.person_id == person.person_id,
            ProfessionalRoleProfile.crn == crn,
            ProfessionalRoleProfile.role_title == role_title
        ).first()

        if not existing:
            profile = ProfessionalRoleProfile(
                person_id=person.person_id,
                crn=crn,
                role_title=role_title,
                standardized_role=std_role,
                seniority_level="c_level" if std_role in ("finance_director", "managing_director") else "director",
                is_finance_decision_maker=is_finance_decision_maker,
                is_current=is_current,
                start_date=start_date,
                platform=platform,
                source_provider=source_provider,
                evidence_summary=json.dumps(evidence),
                match_confidence=0.90,  # Statutory register has high primary match confidence
                verification_status="candidate" if not is_finance_decision_maker else "candidate",
                last_verified_at=datetime.now(timezone.utc)
            )
            self.db.add(profile)
            self.db.flush()
            return profile
        else:
            existing.is_current = is_current
            existing.is_finance_decision_maker = is_finance_decision_maker or existing.is_finance_decision_maker
            existing.last_verified_at = datetime.now(timezone.utc)
            return existing
