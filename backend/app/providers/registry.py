from typing import Dict, List, Optional
from backend.app.providers.base import IProviderAdapter, ProviderStatus
from backend.app.providers.companies_house import CompaniesHouseAdapter
from backend.app.providers.creditsafe import CreditsafeAdapter
from backend.app.providers.experian import ExperianAdapter
from backend.app.providers.workforce import WorkforceAdapter
from backend.app.providers.contracts_finder import ContractsFinderAdapter
from backend.app.providers.the_gazette import GazetteAdapter

class ProviderRegistry:
    """Central registry and health monitor for all external signal providers."""

    def __init__(self):
        self.companies_house = CompaniesHouseAdapter()
        self.creditsafe = CreditsafeAdapter()
        self.experian = ExperianAdapter()
        self.workforce = WorkforceAdapter()
        self.contracts_finder = ContractsFinderAdapter()
        self.the_gazette = GazetteAdapter()

    def get_all_providers(self) -> Dict[str, IProviderAdapter]:
        return {
            "companies_house": self.companies_house,
            "creditsafe": self.creditsafe,
            "experian": self.experian,
            "workforce_intelligence": self.workforce,
            "contracts_finder": self.contracts_finder,
            "the_gazette": self.the_gazette,
        }

    def get_adapter(self, provider_name: str) -> Optional[IProviderAdapter]:
        return self.get_all_providers().get(provider_name)

    def get_health_summary(self) -> List[ProviderStatus]:
        return [
            self.companies_house.get_health_status(),
            self.contracts_finder.get_health_status(),
            self.the_gazette.get_health_status(),
            self.creditsafe.get_health_status(),
            self.experian.get_health_status(),
            self.workforce.get_health_status(),
        ]

provider_registry = ProviderRegistry()
