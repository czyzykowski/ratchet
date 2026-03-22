"""Job dispatcher: matches ready tasks to available workers and initiates remote execution."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from uuid import UUID

from core import events as ev
from core import git_transfer
from core.execution_manager import ExecutionManager
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.remote_protocol import (
    AssignTaskMessage,
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from orchestrator.registry import WorkerConnection, WorkerRegistry

logger = logging.getLogger(__name__)


async def dispatch_pending(store: Store, registry: WorkerRegistry) -> int:
    """Discover ready tasks, match to available workers, and dispatch.

    Returns the count of tasks dispatched in this pass.
    """
    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    spec_manager = SpecManager(store)

    active_projects = await project_manager.list_projects()
    candidates: list[tuple[Task, Project, Spec]] = []

    for project in active_projects:
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
            task = await task_manager.get_task(task_id)
            if task is None or task.status != ev.READY_FOR_IMPLEMENTATION:
                continue
            spec = await spec_manager.get_current_spec(task_id)
            if spec is None:
                logger.warning("Task %s has no spec assigned, skipping", task_id)
                continue
            candidates.append((task, project, spec))

    candidates.sort(key=lambda c: c[0].created_at)

    from worker.capability_check import effective_capabilities

    count = 0
    for task, project, spec in candidates:
        required = list(effective_capabilities(task, project))
        worker = registry.find_available(required)
        if worker is None:
            continue
        try:
            await _dispatch_one(task, project, spec, worker, store, registry)
            count += 1
        except Exception:
            logger.exception(
                "dispatch_pending: failed to dispatch task=%s to worker=%s",
                task.id,
                worker.worker_id,
            )

    return count


async def _dispatch_one(
    task: Task,
    project: Project,
    spec: Spec,
    worker: WorkerConnection,
    store: Store,
    registry: WorkerRegistry,
) -> None:
    """Dispatch a single task to a worker: create execution, bundle, send, assign, transition."""
    execution_manager = ExecutionManager(store, project.local_path)
    execution = await execution_manager.start_execution(task.id, spec.id, project=project)
    worktree_path = os.path.join(project.local_path, ".worktrees", str(execution.id))
    bundle_bytes = git_transfer.create_bundle(worktree_path)
    git_bundle_b64 = base64.b64encode(bundle_bytes).decode()
    msg = AssignTaskMessage(
        type="assign_task",
        task_id=str(task.id),
        spec_id=str(spec.id),
        spec_content=spec.content,
        project_id=str(project.id),
        project_name=project.name,
        project_local_path=project.local_path,
        project_intent_md=project.intent_md,
        project_ratchet_yaml=project.ratchet_yaml,
        project_config_source=project.config_source,
        git_bundle_b64=git_bundle_b64,
    )
    await worker.websocket.send_text(msg.model_dump_json())
    registry.assign_job(worker.worker_id, str(execution.id))
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task.id, ev.IN_PROGRESS)
    logger.info(
        "Dispatched task %s to worker %s (execution %s)",
        task.id,
        worker.worker_id,
        execution.id,
    )


async def dispatch_loop(
    store: Store, registry: WorkerRegistry, interval_seconds: float = 2.0
) -> None:
    """Run dispatch_pending repeatedly at the given interval until cancelled."""
    while True:
        try:
            await asyncio.wait_for(dispatch_pending(store, registry), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("dispatch_pending timed out")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("dispatch_pending failed, will retry", exc_info=True)
        await asyncio.sleep(interval_seconds)


class JobDispatcher:
    """Backward-compatible dispatcher class used by the orchestrator server."""

    def __init__(self, registry: WorkerRegistry) -> None:
        self._registry = registry

    async def dispatch_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(2.0)
                logger.debug("dispatch loop running (no store configured)")
        except asyncio.CancelledError:
            raise

    def handle_execution_started(self, worker_id: str, msg: ExecutionStartedMessage) -> None:
        logger.debug("execution started: worker=%s execution=%s", worker_id, msg.execution_id)

    def handle_execution_completed(self, worker_id: str, msg: ExecutionCompletedMessage) -> None:
        logger.debug("execution completed: worker=%s execution=%s", worker_id, msg.execution_id)

    def handle_execution_failed(self, worker_id: str, msg: ExecutionFailedMessage) -> None:
        logger.debug(
            "execution failed: worker=%s execution=%s reason=%s",
            worker_id,
            msg.execution_id,
            msg.failure_reason,
        )
