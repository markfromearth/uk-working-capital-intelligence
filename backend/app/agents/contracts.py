"""
Typed Envelopes and Contracts for Multi-Agent Orchestration.
Guarantees typed versioned inputs, outputs, budget enforcement, and provenance tracking.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    SIGNAL_DETECTION = "SIGNAL_DETECTION"                # Agent A
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"              # Agent B
    ADAPTIVE_INVESTIGATION = "ADAPTIVE_INVESTIGATION"    # Agent C
    FINANCE_OPPORTUNITY = "FINANCE_OPPORTUNITY"          # Agent D
    MARKETING_RESEARCH = "MARKETING_RESEARCH"            # Agent E
    AUDIENCE_STRATEGY = "AUDIENCE_STRATEGY"              # Agent F
    CAMPAIGN_ECONOMICS = "CAMPAIGN_ECONOMICS"            # Agent G
    CAMPAIGN_MEASUREMENT = "CAMPAIGN_MEASUREMENT"        # Agent H
    COMPLIANCE_RISK = "COMPLIANCE_RISK"                  # Agent I
    INDEPENDENT_VERIFICATION = "INDEPENDENT_VERIFICATION"# Agent J


class AgentTaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    REJECTED = "REJECTED"


class AgentTaskRequest(BaseModel):
    """Immutable input envelope dispatched to a specialized agent."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_role: AgentRole
    crn: str
    budget_limit_gbp: float = 0.00  # Default £0.00 for free operating mode
    timeout_seconds: float = 15.0
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    provenance_chain: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentTaskResult(BaseModel):
    """Verified structured output returned by an executed agent."""
    task_id: str
    correlation_id: str
    role: AgentRole
    status: AgentTaskStatus
    crn: str
    cost_incurred_gbp: float = 0.00
    duration_ms: float = 0.00
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_references: List[Dict[str, Any]] = Field(default_factory=list)
    is_verified: bool = False
    verification_notes: Optional[str] = None
    error_message: Optional[str] = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MultiAgentExecutionTrace(BaseModel):
    """Full auditable provenance record of a multi-agent investigation journey."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    crn: str
    company_name: Optional[str] = None
    total_budget_gbp: float
    total_cost_gbp: float = 0.00
    total_duration_ms: float = 0.00
    is_fully_verified: bool = False
    rejection_reason: Optional[str] = None
    task_traces: List[AgentTaskResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
