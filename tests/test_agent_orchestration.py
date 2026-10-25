"""
Test Suite for Decoupled Multi-Agent Architecture (Agents A, B, C, D, J).
Verifies typed task contracts, dependency graphs, budget enforcement,
error containment, timeouts, and independent verification audits.
"""

import asyncio
import pytest
from datetime import datetime, timezone

from backend.app.agents.contracts import (
    AgentRole,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskStatus,
    MultiAgentExecutionTrace,
)
from backend.app.agents.signal_detection import SignalDetectionAgent
from backend.app.agents.entity_resolution import EntityResolutionAgent
from backend.app.agents.adaptive_investigation import AdaptiveInvestigationAgent
from backend.app.agents.opportunity_assessment import FinanceOpportunityAgent
from backend.app.agents.independent_verification import IndependentVerificationAgent
from backend.app.agents.orchestrator import MultiAgentOrchestrator, default_agent_orchestrator
from backend.app.database import init_db
from backend.app.providers.contracts_finder import ContractsFinderAdapter
from backend.app.providers.registry import provider_registry


@pytest.fixture(scope="module", autouse=True)
def ensure_agent_db_initialized():
    init_db()


@pytest.mark.asyncio
async def test_agent_contracts_and_serialization():
    req = AgentTaskRequest(
        target_role=AgentRole.SIGNAL_DETECTION,
        crn="08931201",
        budget_limit_gbp=5.00,
        input_payload={"test_param": "value"},
    )
    dumped = req.model_dump()
    assert dumped["target_role"] == "SIGNAL_DETECTION"
    assert dumped["crn"] == "08931201"
    assert dumped["budget_limit_gbp"] == 5.00

    loaded = AgentTaskRequest.model_validate(dumped)
    assert loaded.task_id == req.task_id


@pytest.mark.asyncio
async def test_signal_detection_agent_execution():
    agent = SignalDetectionAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.SIGNAL_DETECTION,
        crn="08931201",
        budget_limit_gbp=0.00,
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert result.role == AgentRole.SIGNAL_DETECTION
    assert result.cost_incurred_gbp == 0.00
    assert "signals_detected_count" in result.output_payload


@pytest.mark.asyncio
async def test_entity_resolution_agent_execution():
    agent = EntityResolutionAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.ENTITY_RESOLUTION,
        crn="08931201",
        budget_limit_gbp=0.00,
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert result.output_payload["canonical_crn"] == "08931201"
    assert len(result.evidence_references) >= 1
    assert result.evidence_references[0]["source"] == "COMPANIES_HOUSE_REGISTRY"


@pytest.mark.asyncio
async def test_adaptive_investigation_agent_execution():
    agent = AdaptiveInvestigationAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.ADAPTIVE_INVESTIGATION,
        crn="08931201",
        budget_limit_gbp=0.00,
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert result.cost_incurred_gbp == 0.00
    assert result.output_payload["total_tools_evaluated"] >= 5


@pytest.mark.asyncio
async def test_finance_opportunity_agent_execution():
    agent = FinanceOpportunityAgent()
    req = AgentTaskRequest(
        target_role=AgentRole.FINANCE_OPPORTUNITY,
        crn="08931201",
        budget_limit_gbp=0.00,
        input_payload={"company_name": "Apex Precision Engineering Ltd"},
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert "p_qualified_opportunity" in result.output_payload
    assert "estimated_facility_size_gbp" in result.output_payload
    assert result.output_payload["estimated_facility_size_gbp"] > 0.0


@pytest.mark.asyncio
async def test_independent_verification_agent_passes_on_valid_evidence():
    agent = IndependentVerificationAgent()
    mock_results = [
        {
            "role": AgentRole.SIGNAL_DETECTION.value,
            "cost_incurred_gbp": 0.00,
            "output_payload": {"signals_detected_count": 1},
            "evidence_references": [{"source": "COMPANIES_HOUSE", "uri": "https://example.com/ch/1"}],
        },
        {
            "role": AgentRole.FINANCE_OPPORTUNITY.value,
            "cost_incurred_gbp": 0.00,
            "output_payload": {"p_qualified_opportunity": 0.75},
            "evidence_references": [{"source": "COMPANIES_HOUSE", "uri": "https://example.com/ch/1"}],
        },
    ]

    req = AgentTaskRequest(
        target_role=AgentRole.INDEPENDENT_VERIFICATION,
        crn="08931201",
        budget_limit_gbp=0.00,
        input_payload={"prior_results": mock_results},
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert result.output_payload["is_fully_verified"] is True
    assert result.output_payload["verification_status"] == "PASSED"
    assert result.output_payload["failures_count"] == 0


@pytest.mark.asyncio
async def test_independent_verification_agent_rejects_ungrounded_claims():
    """Verify that Agent J catches claims made without evidence references or violating budget."""
    agent = IndependentVerificationAgent()
    mock_results = [
        {
            "role": AgentRole.SIGNAL_DETECTION.value,
            "cost_incurred_gbp": 0.00,
            "output_payload": {"signals_detected_count": 5},
            "evidence_references": [],  # Claimed 5 signals but 0 evidence references!
        },
        {
            "role": AgentRole.FINANCE_OPPORTUNITY.value,
            "cost_incurred_gbp": 12.50,  # Violated free £0.00 operating mode!
            "output_payload": {"p_qualified_opportunity": 0.95},
            "evidence_references": [],
        },
    ]

    req = AgentTaskRequest(
        target_role=AgentRole.INDEPENDENT_VERIFICATION,
        crn="08931201",
        budget_limit_gbp=0.00,
        input_payload={"prior_results": mock_results},
    )
    result = await agent.execute(req)

    assert result.status == AgentTaskStatus.COMPLETED
    assert result.output_payload["is_fully_verified"] is False
    assert result.output_payload["verification_status"] == "REJECTED"
    assert result.output_payload["failures_count"] >= 2
    failures = result.output_payload["failures"]
    assert any("Free operating mode violated" in f for f in failures)
    assert any("0 evidence references" in f for f in failures)


@pytest.mark.asyncio
async def test_multi_agent_orchestrator_pipeline_end_to_end():
    orchestrator = MultiAgentOrchestrator()
    trace = await orchestrator.run_investigation_pipeline(
        crn="08931201",
        total_budget_gbp=0.00,
    )

    assert isinstance(trace, MultiAgentExecutionTrace)
    assert trace.crn == "08931201"
    assert trace.total_cost_gbp == 0.00  # Zero cost in free mode
    assert len(trace.task_traces) == 10

    # Check execution order: A -> B -> C -> D -> E -> F -> G -> H -> I -> J
    roles = [t.role for t in trace.task_traces]
    assert roles == [
        AgentRole.SIGNAL_DETECTION,
        AgentRole.ENTITY_RESOLUTION,
        AgentRole.ADAPTIVE_INVESTIGATION,
        AgentRole.FINANCE_OPPORTUNITY,
        AgentRole.MARKETING_RESEARCH,
        AgentRole.AUDIENCE_STRATEGY,
        AgentRole.CAMPAIGN_ECONOMICS,
        AgentRole.CAMPAIGN_MEASUREMENT,
        AgentRole.COMPLIANCE_RISK,
        AgentRole.INDEPENDENT_VERIFICATION,
    ]

    for t in trace.task_traces:
        assert t.status == AgentTaskStatus.COMPLETED
        assert t.duration_ms >= 0.0

    assert trace.is_fully_verified is True
    assert trace.rejection_reason is None
