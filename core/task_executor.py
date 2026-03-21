"""TaskExecutor: collapses the 4-module execution pipeline into a single class."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from uuid import UUID, uuid4

from core.context_assembler import ContextAssembler, ContextAssemblyError
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.models import Project, Spec, Task


class ExecutionOutcome(Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ENV_ERROR = "env_error"


@dataclass(frozen=True)
class ExecutionResult:
    outcome: ExecutionOutcome
    execution_id: UUID
    failure_reason: str | None
    trace_id: UUID | None


class TaskExecutor:
    def __init__(
        self,
        execution_manager: ExecutionManager,
        context_assembler: ContextAssembler,
        invoker: ClaudeCodeInvoker,
    ) -> None:
        self._em = execution_manager
        self._ca = context_assembler
        self._invoker = invoker

    async def execute(
        self, task: Task, spec: Spec, project: Project
    ) -> ExecutionResult:
        try:
            execution = await self._em.start_execution(task.id, spec.id, project)
        except OSError as exc:
            return ExecutionResult(
                outcome=ExecutionOutcome.ENV_ERROR,
                execution_id=uuid4(),
                failure_reason=str(exc),
                trace_id=None,
            )
        return await self._run_invocation(task, project, execution.id)

    async def resume(
        self, task: Task, project: Project, execution_id: UUID
    ) -> ExecutionResult:
        return await self._run_invocation(task, project, execution_id)

    async def _run_invocation(
        self, task: Task, project: Project, execution_id: UUID
    ) -> ExecutionResult:
        try:
            context = await self._ca.assemble(execution_id, project)
        except ContextAssemblyError as exc:
            reason = str(exc)
            await self._em.fail_execution(execution_id, reason)
            return ExecutionResult(
                outcome=ExecutionOutcome.ENV_ERROR,
                execution_id=execution_id,
                failure_reason=reason,
                trace_id=None,
            )

        try:
            inv = await asyncio.to_thread(self._invoker.invoke, context)
        except Exception as exc:
            await self._em.fail_execution(
                execution_id, f"unexpected error: {exc}"
            )
            raise

        if inv.status == "completed":
            await self._em.complete_execution(execution_id)
            return ExecutionResult(
                outcome=ExecutionOutcome.COMPLETED,
                execution_id=execution_id,
                failure_reason=None,
                trace_id=inv.trace_id,
            )
        reason = inv.failure_reason or inv.status
        await self._em.fail_execution(execution_id, reason)
        return ExecutionResult(
            outcome=ExecutionOutcome.BLOCKED,
            execution_id=execution_id,
            failure_reason=reason,
            trace_id=inv.trace_id,
        )
