"""ImplPipeline: find a ready task, check baseline QA, execute implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from worker.dispatcher import DispatchResult

from core import events as ev
from core import qa_manager
from core.context_assembler import ContextAssembler
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.managers import Managers
from core.models import Project, Spec, Task
from core.qa_runner import check_baseline_qa
from core.task_executor import TaskExecutor
from worker.capability_check import capabilities_met
from worker.event_helpers import (
    apply_execution_outcome,
    has_pending_baseline_qa_failure,
    should_skip_baseline_qa,
)
from worker.task_finder import TaskFinder
from worker.worktree import (
    QAWorktreeError,
    create_baseline_worktree,
    remove_qa_worktree,
)

logger = logging.getLogger(__name__)


class ImplPipeline:
    def __init__(
        self,
        managers: Managers,
        invoker: ClaudeCodeInvoker,
        task_finder: TaskFinder,
        capabilities: list[str],
    ) -> None:
        self._store = managers.store
        self._managers = managers
        self._invoker = invoker
        self._task_finder = task_finder
        self._capabilities = capabilities
        self._task_manager = managers.tasks
        self._spec_manager = managers.specs
        self._state_machine = managers.state_machine

    async def run(self, project_id: UUID | None = None) -> DispatchResult:
        from worker.dispatcher import DispatchResult  # runtime import to avoid circular

        candidates = await self._task_finder.find(
            statuses={ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT},
            project_id=project_id,
            skip_project_if_status=ev.IN_PROGRESS,
        )

        # Async secondary filters
        task: Task | None = None
        project: Project | None = None
        spec: Spec | None = None
        for t, p, task_events in candidates:
            if not capabilities_met(t, p, self._capabilities):
                continue
            if t.status == ev.WAITING_FOR_INPUT:
                pending = await qa_manager.get_pending_question(self._store, t.id)
                if pending is not None:
                    continue

            if t.depends_on:
                unmet = False
                for dep_id_str in t.depends_on:
                    try:
                        dep_id = UUID(dep_id_str)
                    except ValueError:
                        logger.debug(
                            "Task %s has invalid dep UUID %s, skipping",
                            t.id,
                            dep_id_str,
                        )
                        unmet = True
                        break
                    dep_task = await self._task_manager.get_task(dep_id)
                    if dep_task is None or dep_task.status != ev.DEPLOYED:
                        logger.debug(
                            "Task %s skipped: dep %s not deployed", t.id, dep_id_str
                        )
                        unmet = True
                        break
                if unmet:
                    continue

            s = await self._spec_manager.get_current_spec(t.id)
            if s is None:
                logger.warning("Task %s has no spec assigned, skipping", t.id)
                continue

            task, project, spec = t, p, s
            break

        if task is None or project is None or spec is None:
            logger.info("No tasks ready for implementation.")
            return DispatchResult(action="idle")

        # Resume path for waiting_for_input tasks
        if task.status == ev.WAITING_FOR_INPUT:
            execution_manager = ExecutionManager(self._store, project.local_path)
            execution = await execution_manager.get_current_execution(task.id)
            if execution is None:
                logger.error(
                    "No running execution for waiting_for_input task=%s, blocking",
                    task.id,
                )
                await self._state_machine.transition(
                    task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
                )
                await self._state_machine.transition(task.id, ev.BLOCKED)
                return DispatchResult(action="impl", task_id=task.id, success=False)
            await self._state_machine.transition(
                task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
            )
            logger.info(
                "Resuming execution after input: task=%s execution=%s",
                task.id,
                execution.id,
            )
            executor = TaskExecutor(
                execution_manager, ContextAssembler(self._store), self._invoker
            )
            exec_result = await executor.resume(task, project, execution.id)
            await apply_execution_outcome(self._state_machine, task, exec_result)
            return DispatchResult(action="impl", task_id=task.id)

        # Baseline QA check
        task_events = await self._store.get_events(task.id, "task")
        skip_baseline = should_skip_baseline_qa(task_events)

        if not skip_baseline:
            ratchet_yaml = (
                project.ratchet_yaml if project.config_source == "db" else None
            )
            try:
                baseline_worktree = await asyncio.to_thread(
                    create_baseline_worktree, project.local_path
                )
            except QAWorktreeError as exc:
                logger.warning(
                    "Baseline QA worktree creation failed for project=%s: %s"
                    " — skipping baseline check",
                    project.name,
                    exc,
                )
                baseline_failures: list[Any] = []
            else:
                try:
                    baseline_failures = await asyncio.to_thread(
                        check_baseline_qa, baseline_worktree, ratchet_yaml
                    )
                finally:
                    await asyncio.to_thread(
                        remove_qa_worktree, project.local_path, baseline_worktree
                    )
            if baseline_failures:
                combined = "\n\n".join(
                    f"Step '{r.step_name}':\n{r.output}" for r in baseline_failures
                )
                logger.warning(
                    "Baseline QA failed for project=%s — skipping task=%s.\n%s",
                    project.name,
                    task.id,
                    combined,
                )
                if not has_pending_baseline_qa_failure(task_events):
                    await self._store.append_event(
                        aggregate_id=task.id,
                        aggregate_type="task",
                        event_type=ev.TASK_BASELINE_QA_FAILED,
                        payload={"failure_output": combined},
                    )
                return DispatchResult(action="idle")

        execution_manager = ExecutionManager(self._store, project.local_path)

        logger.info(
            "Starting execution: task=%s project=%s spec=%s",
            task.id,
            project.name,
            spec.id,
        )

        await self._state_machine.transition(
            task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
        )

        executor = TaskExecutor(
            execution_manager, ContextAssembler(self._store), self._invoker
        )
        exec_result = await executor.execute(task, spec, project)

        logger.info(
            "Execution %s: task=%s execution=%s trace=%s",
            exec_result.outcome.value,
            task.id,
            exec_result.execution_id,
            exec_result.trace_id,
        )

        await apply_execution_outcome(self._state_machine, task, exec_result)
        return DispatchResult(action="impl", task_id=task.id)
