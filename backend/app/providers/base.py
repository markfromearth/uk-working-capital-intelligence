from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class AdapterHealthStatus(str, Enum):
    LIVE = "live"
    SIMULATED = "simulated"
    UNCONFIGURED = "unconfigured"
    ERROR = "error"

class ProviderStatus(BaseModel):
    provider_name: str
    status: AdapterHealthStatus
    endpoint: Optional[str] = None
    rate_limit_remaining: Optional[int] = None
    last_event_timestamp: Optional[datetime] = None
    message: Optional[str] = None

class IProviderAdapter(ABC):
    """Common interface for external data provider adapters."""
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'companies_house', 'creditsafe', 'proxycurl')."""
        pass

    @abstractmethod
    def get_health_status(self) -> ProviderStatus:
        """Check provider status (live with real credentials vs deterministic simulation vs unconfigured)."""
        pass

    @abstractmethod
    def fetch_company_signals(self, crn: str) -> List[Dict[str, Any]]:
        """Fetch raw or normalised signals for a specific UK company CRN."""
        pass

class IStreamingAdapter(ABC):
    """Interface for streaming or event-driven ingestion."""

    @abstractmethod
    def poll_stream_events(self, timepoint: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """
        Poll or stream events from documented resume cursor/timepoint.
        Returns dict containing 'events': list, 'next_timepoint': str, 'has_more': bool.
        """
        pass
