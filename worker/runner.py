"""Worker runner: single-pass task execution connecting all core components."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from core import events as ev
from core.context_assembler import ContextAssembler, ContextAssemblyError
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store

logger = logging.getLogger(__name__)


def _build_task_from_events(task_id: UUID, project_id: UUID, events: list) -> Task | None:
    """Replay task events to build a Task model. Returns None if no TASK_CREATED event found."""
    task: Task | None = None
    current_spec_id: UUID | None = None

    for event in events:
        if event.event_type == ev.TASK_CREATED:
            p = event.payload
            task = Task(
                id=task_id,
                project_id=project_id,
                title=p.get("title", ""),
                status=p.get("status", ev.READY_FOR_SPEC),
                current_spec_id=None,
                refinement_count=p.get("refinement_count", 0),
                created_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        elif event.event_type == ev.TASK_STATUS_CHANGED:
            if task is not None:
                task = task.model_copy(
                    update={
                        "status": event.payload["to_status"],
                        "updated_at": event.occurred_at,
                    }
                )
        elif event.event_type == ev.TASK_SPEC_ASSIGNED:
            spec_id_str = event.payload.get("spec_id")
            current_spec_id = UUID(spec_id_str) if spec_id_str else None
            if task is not None:
                task = task.model_copy(update={"current_spec_id": current_spec_id})

    return task


async def get_next_task(
    store: Store,
    project_manager: ProjectManager,
    spec_manager: SpecManager,
    state_machine: TaskStateMachine,
) -> tuple[Task, Project, Spec] | None:
    """Find oldest ready_for_implementation task with active project and assigned spec.

    Returns (task, project, spec) tuple or None if nothing ready.

    Algorithm:
    1. Get all active projects via project_manager.list_projects()
    2. For each project, replay task events to find tasks in ready_for_implementation
    3. For each candidate task, verify it has a current spec via spec_manager.get_current_spec()
    4. Skip tasks with no spec — log warning "Task <id> has no spec assigned, skipping"
    5. Collect all valid (task, project, spec) candidates
    6. Return candidate with oldest task.created_at, or None if empty
    """
    active_projects = await project_manager.list_projects()
    candidates: list[tuple[Task, Project, Spec]] = []

    for project in active_projects:
        # Discover task_ids for this project via the project_tasks registry.
        # Tasks are dual-written to (project.id, "project_tasks") at creation time.
        project_task_events = await store.get_events(project.id, "project_tasks")

        task_ids_seen: set[UUID] = set()
        task_ids_ordered: list[UUID] = []
        for event in project_task_events:
            tid_str = event.payload.get("task_id")
            if tid_str:
                tid = UUID(tid_str)
                if tid not in task_ids_seen:
                    task_ids_seen.add(tid)
                    task_ids_ordered.append(tid)

        for task_id in task_ids_ordered:
            task_events = await store.get_events(task_id, "task")
            if not task_events:
                continue

            task = _build_task_from_events(task_id, project.id, task_events)
            if task is None or task.status != ev.READY_FOR_IMPLEMENTATION:
                continue

            spec = await spec_manager.get_current_spec(task_id)
            if spec is None:
                logger.warning("Task %s has no spec assigned, skipping", task_id)
                continue

            candidates.append((task, project, spec))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0].created_at)
    return candidates[0]


async def run_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None,
) -> None:
    """Single-pass task execution.

    invoker parameter allows injection for testing — defaults to ClaudeCodeInvoker().
    """
    if invoker is None:
        invoker = ClaudeCodeInvoker()

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, project_manager, spec_manager, state_machine)
    if result is None:
        logger.info("No tasks ready for implementation. Exiting.")
        return

    task, project, spec = result
    execution_manager = ExecutionManager(store, project.local_path)
    context_assembler = ContextAssembler(store)

    logger.info(
        "Starting execution: task=%s project=%s spec=%s",
        task.id,
        project.name,
        spec.id,
    )

    await state_machine.transition(task.id, ev.IN_PROGRESS)

    try:
        execution = await execution_manager.start_execution(task.id, spec.id)
    except OSError as exc:
        failure_reason = str(exc)
        logger.error(
            "Environment preparation failed: task=%s reason=%s", task.id, failure_reason
        )
        await state_machine.transition(task.id, ev.BLOCKED)
        return

    execution_id = execution.id
    logger.info("Execution started: task=%s execution=%s", task.id, execution_id)

    try:
        context = await context_assembler.assemble(execution_id)
    except ContextAssemblyError as exc:
        failure_reason = str(exc)
        logger.error(
            "Context assembly failed: task=%s execution=%s reason=%s",
            task.id,
            execution_id,
            failure_reason,
        )
        await execution_manager.fail_execution(execution_id, failure_reason)
        await state_machine.transition(task.id, ev.BLOCKED)
        return

    try:
        invocation_result = await asyncio.to_thread(invoker.invoke, context)
    except Exception as exc:
        failure_reason = f"unexpected error: {exc}"
        logger.error("Unexpected error: task=%s error=%s", task.id, str(exc))
        await execution_manager.fail_execution(execution_id, failure_reason)
        await state_machine.transition(task.id, ev.BLOCKED)
        raise

    if invocation_result.status == "completed":
        await execution_manager.complete_execution(execution_id)
        await state_machine.transition(task.id, ev.READY_FOR_QA)
        logger.info(
            "Execution completed: task=%s trace=%s",
            task.id,
            invocation_result.trace_path,
        )
    elif invocation_result.status in ("failed", "crashed"):
        failure_reason = invocation_result.failure_reason or invocation_result.status
        await execution_manager.fail_execution(execution_id, failure_reason)
        await state_machine.transition(task.id, ev.BLOCKED)
        logger.info("Execution failed: task=%s reason=%s", task.id, failure_reason)


def main() -> None:
    """Initialize all components with PostgresStore and run once."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main_async())


async def _main_async() -> None:
    from core.db import close_pool
    from core.store import PostgresStore

    store = PostgresStore()
    invoker = ClaudeCodeInvoker()
    try:
        await run_once(store, invoker)
    finally:
        await close_pool()
