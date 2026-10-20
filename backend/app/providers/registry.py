from typing import Dict, List
from backend.app.providers.base import IProviderAdapter, ProviderStatus
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.experian import ExperianAdapter
from backend.app.providers.workforce import WorkforceAdapter

class ProviderRegistry:
    """Central registry and health monitor for all external signal providers."""

    def __init__(self):
        self.companies_house = CompaniesHouseAdapter()
        self.creditsafe = CreditsafeAdapter()
        self.experian = ExperianAdapter()
        self.workforce = WorkforceAdapter()

    def get_all_providers(self) -> Dict[str, IProviderAdapter]:
        return {
            "companies_house": self.companies_house,
            "creditsafe": self.creditsafe,
            "experian": self.experian,
            "workforce_intelligence": self.workforce,
        }

    def get_health_summary(self) -> List[ProviderStatus]:
        return [
            self.companies_house.get_health_status(),
            self.creditsafe.get_health_status(),
            self.experian.get_health_status(),
            self.workforce.get_health_status(),
        ]

provider_registry = ProviderRegistry()
