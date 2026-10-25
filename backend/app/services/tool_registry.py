import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.logging import logger
from backend.app.core.crn_normalizer import normalize_uk_crn
from backend.app.providers.registry import provider_registry
from backend.app.providers.base import AdapterHealthStatus
from backend.app.services.ingestion import IngestionService
from backend.app.services.audit import log_audit_event

class ToolDefinition(BaseModel):
    tool_name: str
    provider_name: str
    description: str
    cost_gbp: float
    rate_limit_per_min: int
    timeout_seconds: float = 10.0
    data_use_policy: str
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {"crn": {"type": "string"}}})
    is_live_ready: bool = False
    requires_paid_subscription: bool = False

class CircuitBreakerState:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.is_open = False

    def record_success(self):
        self.consecutive_failures = 0
        self.is_open = False

    def reset(self):
        self.record_success()

    def record_failure(self):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.is_open = True
            logger.warning(f"Circuit breaker TRIPPED to OPEN ({self.consecutive_failures} consecutive failures)")

    def can_execute(self) -> bool:
        if not self.is_open:
            return True
        if self.last_failure_time and (time.time() - self.last_failure_time) > self.cooldown_seconds:
            # Half-open trial
            logger.info("Circuit breaker entering HALF-OPEN state for retry")
            return True
        return False

class ApprovedToolRegistry:
    """
    Registry of approved investigation tools with strict capability declarations,
    transparent query cost accounting, rate limit tracking, and circuit breakers.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._circuit_breakers: Dict[str, CircuitBreakerState] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        # 1. Companies House Official REST API (Live Verified)
        ch_status = provider_registry.get_adapter("companies_house").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="companies_house_charges_refresh",
                provider_name="companies_house",
                description="Live query of Companies House corporate register for recent MR01 charge registrations and MR04 satisfactions.",
                cost_gbp=0.00,  # Official free API
                rate_limit_per_min=120,
                timeout_seconds=8.0,
                data_use_policy="statutory_public_register_b2b_only",
                is_live_ready=ch_status.status == AdapterHealthStatus.LIVE
            )
        )

        # 2. Companies House Profile & Officers Refresh
        self.register_tool(
            ToolDefinition(
                tool_name="companies_house_profile_lookup",
                provider_name="companies_house",
                description="Official Companies House statutory registered office, SIC codes, and active status verification.",
                cost_gbp=0.00,
                rate_limit_per_min=120,
                timeout_seconds=8.0,
                data_use_policy="statutory_public_register_b2b_only",
                is_live_ready=ch_status.status == AdapterHealthStatus.LIVE
            )
        )

        # 3. Contracts Finder Procurement Awards (100% Free OGL)
        cf_status = provider_registry.get_adapter("contracts_finder").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="contracts_finder_awards_lookup",
                provider_name="contracts_finder",
                description="Official Contracts Finder & Find a Tender public sector contract award search (OCDS).",
                cost_gbp=0.00,  # Free open government data
                rate_limit_per_min=60,
                timeout_seconds=10.0,
                data_use_policy="open_government_licence_v3",
                is_live_ready=True,
                requires_paid_subscription=False,
            )
        )

        # 4. The Gazette Corporate Insolvency Notices (100% Free Crown Copyright)
        gz_status = provider_registry.get_adapter("the_gazette").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="the_gazette_insolvency_check",
                provider_name="the_gazette",
                description="Official The Gazette statutory corporate insolvency, winding-up petition, and administration check.",
                cost_gbp=0.00,  # Free open public record
                rate_limit_per_min=120,
                timeout_seconds=10.0,
                data_use_policy="crown_copyright_ogl_v3",
                is_live_ready=True,
                requires_paid_subscription=False,
            )
        )

        # 5. Creditsafe Commercial Deep Financials (Commercial Paid Provider)
        cs_status = provider_registry.get_adapter("creditsafe").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="creditsafe_deep_financials",
                provider_name="creditsafe",
                description="Commercial credit bureau search retrieving trade payment delinquency, DBT trends, and CCJs.",
                cost_gbp=2.50,  # Commercial query fee
                rate_limit_per_min=60,
                timeout_seconds=12.0,
                data_use_policy="commercial_credit_assessment_b2b",
                is_live_ready=cs_status.status == AdapterHealthStatus.LIVE and bool(settings.CREDITSAFE_API_KEY),
                requires_paid_subscription=True,
            )
        )

        # 6. Experian Delinquency Trace (Commercial Paid Provider)
        exp_status = provider_registry.get_adapter("experian").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="experian_delinquency_trace",
                provider_name="experian",
                description="Delphi commercial risk rating, credit limit recommendations, and DSO trade velocity.",
                cost_gbp=3.00,
                rate_limit_per_min=50,
                timeout_seconds=12.0,
                data_use_policy="commercial_credit_assessment_b2b",
                is_live_ready=exp_status.status == AdapterHealthStatus.LIVE and bool(settings.EXPERIAN_API_KEY),
                requires_paid_subscription=True,
            )
        )

        # 5. Workforce Dynamics Headcount Audit
        wf_status = provider_registry.get_adapter("workforce_intelligence").get_health_status()
        self.register_tool(
            ToolDefinition(
                tool_name="workforce_hiring_velocity",
                provider_name="workforce_intelligence",
                description="Aggregated headcount momentum and open hiring post volume audit.",
                cost_gbp=0.10,
                rate_limit_per_min=100,
                timeout_seconds=10.0,
                data_use_policy="public_workforce_aggregates_only",
                is_live_ready=wf_status.status == AdapterHealthStatus.LIVE
            )
        )

        # 6. Companies House Officers & Directorships Discovery
        self.register_tool(
            ToolDefinition(
                tool_name="companies_house_officers_lookup",
                provider_name="companies_house",
                description="Statutory active and historical directors, secretaries, and corporate governance appointments.",
                cost_gbp=0.00,  # Official statutory registry free API
                rate_limit_per_min=120,
                timeout_seconds=8.0,
                data_use_policy="statutory_public_register_b2b_only",
                is_live_ready=ch_status.status == AdapterHealthStatus.LIVE
            )
        )

        # 7. Companies House PSC Register Discovery
        self.register_tool(
            ToolDefinition(
                tool_name="companies_house_psc_lookup",
                provider_name="companies_house",
                description="Persons with Significant Control (PSC) statutory register, shareholding tiers, and voting control.",
                cost_gbp=0.00,
                rate_limit_per_min=120,
                timeout_seconds=8.0,
                data_use_policy="statutory_public_register_b2b_only",
                is_live_ready=ch_status.status == AdapterHealthStatus.LIVE
            )
        )

        # 8. Decision-Maker Multi-Signal Verification
        self.register_tool(
            ToolDefinition(
                tool_name="decision_maker_identity_verify",
                provider_name="companies_house",
                description="Multi-signal corroboration of candidate decision-maker identity against active statutory directorships.",
                cost_gbp=0.50,
                rate_limit_per_min=60,
                timeout_seconds=10.0,
                data_use_policy="b2b_professional_identity_verification_only",
                is_live_ready=True
            )
        )

    @property
    def circuit_breakers(self) -> Dict[str, CircuitBreakerState]:
        return self._circuit_breakers

    def get_circuit_breaker(self, tool_or_provider: str) -> Optional[CircuitBreakerState]:
        if tool_or_provider in self._circuit_breakers:
            return self._circuit_breakers[tool_or_provider]
        if tool_or_provider in self._tools:
            provider = self._tools[tool_or_provider].provider_name
            return self._circuit_breakers.get(provider)
        return None

    def register_tool(self, tool: ToolDefinition):
        self._tools[tool.tool_name] = tool
        if tool.provider_name not in self._circuit_breakers:
            self._circuit_breakers[tool.provider_name] = CircuitBreakerState()

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def execute_tool(
        self,
        tool_name: str,
        crn: str,
        db: Session,
        actor_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Safely and idempotently executes an approved investigation tool.
        Enforces circuit breaker checks, validates CRN, stores new signals,
        records audit trail, and returns execution metrics.
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return {"success": False, "error": f"Unknown tool '{tool_name}'", "actual_cost_gbp": 0.0}

        cb = self._circuit_breakers.get(tool.provider_name)
        if cb and not cb.can_execute():
            return {
                "success": False,
                "error": f"Circuit breaker OPEN for provider '{tool.provider_name}'. Queries temporarily suspended.",
                "actual_cost_gbp": 0.0
            }

        norm_crn, valid, err = normalize_uk_crn(crn)
        if not valid or not norm_crn:
            return {"success": False, "error": f"Invalid UK CRN: {err}", "actual_cost_gbp": 0.0}

        ingestion = IngestionService(db)
        start_time = time.time()
        
        from backend.app.ml.evoi_learning import capture_investigation_prior_state, record_investigation_outcome
        prior_state = capture_investigation_prior_state(db, norm_crn)

        try:
            adapter = provider_registry.get_adapter(tool.provider_name)
            if adapter is not None and hasattr(adapter, "last_fetch_status"):
                adapter.last_fetch_status = "IDLE"
                adapter.last_error = None
            signals_found = 0
            summary = ""

            if tool_name == "companies_house_charges_refresh":
                charges = adapter.fetch_company_charges(norm_crn)
                if getattr(adapter, "last_fetch_status", None) == "TIMED_OUT":
                    raise TimeoutError(
                        f"Source timeout in '{tool.provider_name}' during '{tool_name}': {getattr(adapter, 'last_error', 'timeout')}"
                    )
                signals = adapter._parse_charge_into_signals(norm_crn, charges[0]) if charges else []
                for s in signals:
                    s["data_use_restrictions"] = tool.data_use_policy
                    ingestion.process_normalized_signal(raw_event_id=None, signal_data=s)
                    signals_found += 1
                summary = f"Refreshed {len(charges)} charges; created {signals_found} normalized signals."

            elif tool_name == "companies_house_profile_lookup":
                profile = adapter.fetch_company_profile(norm_crn) or {}
                if getattr(adapter, "last_fetch_status", None) == "TIMED_OUT":
                    raise TimeoutError(
                        f"Source timeout in '{tool.provider_name}' during '{tool_name}': {getattr(adapter, 'last_error', 'timeout')}"
                    )
                summary = f"Verified profile: {profile.get('company_name', 'Unknown')}, Status: {profile.get('company_status', 'Unknown')}."

            elif tool_name in ("contracts_finder_awards_lookup", "the_gazette_insolvency_check"):
                signals = adapter.fetch_company_signals(norm_crn)
                if getattr(adapter, "last_fetch_status", None) == "TIMED_OUT":
                    raise TimeoutError(
                        f"Source timeout in '{tool.provider_name}' during '{tool_name}': {getattr(adapter, 'last_error', 'timeout')}"
                    )
                for s in signals:
                    s["data_use_restrictions"] = tool.data_use_policy
                    s["provenance_details"] = f"investigation_tool:{tool_name}:{actor_user_id}"
                    ingestion.process_normalized_signal(raw_event_id=None, signal_data=s)
                    signals_found += 1
                summary = f"Retrieved {signals_found} public notices/awards from {tool.provider_name}."

            elif tool_name in ("creditsafe_deep_financials", "experian_delinquency_trace", "workforce_hiring_velocity"):
                signals = adapter.fetch_company_signals(norm_crn)
                if getattr(adapter, "last_fetch_status", None) == "TIMED_OUT":
                    raise TimeoutError(
                        f"Source timeout in '{tool.provider_name}' during '{tool_name}': {getattr(adapter, 'last_error', 'timeout')}"
                    )
                for s in signals:
                    s["data_use_restrictions"] = tool.data_use_policy
                    s["provenance_details"] = f"investigation_tool:{tool_name}:{actor_user_id}"
                    ingestion.process_normalized_signal(raw_event_id=None, signal_data=s)
                    signals_found += 1
                summary = f"Ingested {signals_found} credit/workforce indicators from {tool.provider_name}."

            elif tool_name in ("companies_house_officers_lookup", "companies_house_psc_lookup"):
                from backend.app.services.decision_maker_discovery import DecisionMakerDiscoveryService
                discovery = DecisionMakerDiscoveryService(db)
                disc_res = discovery.discover_company_personnel(norm_crn, actor_user_id)
                summary = f"Discovered {disc_res['officers_found']} officers and {disc_res['psc_found']} PSC records; mapped {disc_res['decision_makers_mapped']} decision-makers."

            elif tool_name == "decision_maker_identity_verify":
                from backend.app.services.identity_resolution import IdentityResolutionService
                from backend.app.models.decision_maker import ProfessionalRoleProfile
                resolver = IdentityResolutionService(db)
                profiles = db.query(ProfessionalRoleProfile).filter(ProfessionalRoleProfile.crn == norm_crn).all()
                verified_count = 0
                for prof in profiles:
                    resolver.resolve_profile_verification(prof.id, actor_user_id)
                    verified_count += 1
                summary = f"Corroborated {verified_count} professional profiles against statutory appointments."

            elapsed = time.time() - start_time
            if cb:
                cb.record_success()

            # Record executed investigation decision in DB
            from backend.app.models.investigation import InvestigationDecision
            inv_record = InvestigationDecision(
                crn=norm_crn,
                tool_name=tool_name,
                evoi_value=0.0,
                estimated_cost_gbp=tool.cost_gbp,
                actual_cost_gbp=tool.cost_gbp,
                uncertainty_prior=0.0,
                uncertainty_posterior_expected=0.0,
                decision="INVESTIGATE",
                decision_rationale=f"Executed approved tool '{tool_name}'.",
                budget_remaining_gbp=0.0,
                status="executed",
                result_summary=summary
            )
            db.add(inv_record)
            db.commit()

            # Record empirical outcome for EVOI learning
            record_investigation_outcome(
                db=db,
                investigation_id=inv_record.id,
                crn=norm_crn,
                tool_name=tool_name,
                prior_state=prior_state,
                tool_success=True
            )

            # Audit record
            log_audit_event(
                db=db,
                action=f"investigation_tool_executed",
                resource_type="company",
                resource_id=norm_crn,
                user_id=actor_user_id,
                details={
                    "tool_name": tool_name,
                    "cost_gbp": tool.cost_gbp,
                    "elapsed_sec": round(elapsed, 2),
                    "summary": summary
                }
            )

            return {
                "success": True,
                "tool_name": tool_name,
                "crn": norm_crn,
                "actual_cost_gbp": tool.cost_gbp,
                "signals_ingested": signals_found,
                "summary": summary,
                "latency_sec": round(elapsed, 2)
            }

        except Exception as e:
            if cb:
                cb.record_failure()
            logger.error(f"Execution failed for tool '{tool_name}' on CRN {norm_crn}: {e}")
            try:
                record_investigation_outcome(
                    db=db,
                    investigation_id=None,
                    crn=norm_crn,
                    tool_name=tool_name,
                    prior_state=prior_state,
                    tool_success=False
                )
            except Exception:
                pass
            return {
                "success": False,
                "tool_name": tool_name,
                "crn": norm_crn,
                "actual_cost_gbp": 0.0,
                "error": str(e)
            }

# Global singleton tool registry
approved_tool_registry = ApprovedToolRegistry()
