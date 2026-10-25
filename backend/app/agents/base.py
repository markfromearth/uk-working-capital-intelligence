"""
Base Agent Abstract Class with Budget Enforcement, Timeouts, and Error Containment.
"""

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Tuple

from backend.app.agents.contracts import (
    AgentRole,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskStatus,
)
from backend.app.core.logging import logger


class BaseAgent(ABC):
    """Abstract base class for all specialised agents."""

    def __init__(self, role: AgentRole):
        self.role = role

    async def execute(self, request: AgentTaskRequest) -> AgentTaskResult:
        """
        Executes bounded task with deterministic timeout, budget checking,
        error containment, and duration telemetry.
        """
        start_time = time.time()
        logger.info(f"Agent [{self.role.value}] started task {request.task_id} for CRN {request.crn}")

        try:
            # Wrap execution in deterministic timeout guard
            coro = self._run_task(request)
            output_payload, evidence_refs, cost_gbp = await asyncio.wait_for(
                coro, timeout=request.timeout_seconds
            )

            duration_ms = (time.time() - start_time) * 1000.0

            # Budget overflow check (strictly enforces £0.00 free mode as well as positive budgets)
            if cost_gbp > request.budget_limit_gbp:
                logger.warning(f"Agent [{self.role.value}] exceeded budget (£{cost_gbp:.2f} > £{request.budget_limit_gbp:.2f})")
                return AgentTaskResult(
                    task_id=request.task_id,
                    correlation_id=request.correlation_id,
                    role=self.role,
                    status=AgentTaskStatus.BUDGET_EXCEEDED,
                    crn=request.crn,
                    cost_incurred_gbp=cost_gbp,
                    duration_ms=duration_ms,
                    error_message=f"Cost £{cost_gbp:.2f} exceeded task limit £{request.budget_limit_gbp:.2f}",
                )

            return AgentTaskResult(
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                role=self.role,
                status=AgentTaskStatus.COMPLETED,
                crn=request.crn,
                cost_incurred_gbp=cost_gbp,
                duration_ms=duration_ms,
                output_payload=output_payload,
                evidence_references=evidence_refs,
                is_verified=False,  # Set by Agent J
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000.0
            logger.error(f"Agent [{self.role.value}] timed out after {request.timeout_seconds}s on {request.crn}")
            return AgentTaskResult(
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                role=self.role,
                status=AgentTaskStatus.FAILED,
                crn=request.crn,
                duration_ms=duration_ms,
                error_message=f"Agent timed out after {request.timeout_seconds}s",
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000.0
            logger.error(f"Agent [{self.role.value}] execution failed on {request.crn}: {e}")
            return AgentTaskResult(
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                role=self.role,
                status=AgentTaskStatus.FAILED,
                crn=request.crn,
                duration_ms=duration_ms,
                error_message=str(e),
            )

    @abstractmethod
    async def _run_task(
        self, request: AgentTaskRequest
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], float]:
        """
        Perform agent reasoning / tool calls.
        Returns: (output_payload, evidence_references, cost_gbp)
        """
        pass
