"""Job dispatcher: matches ready tasks to available workers and initiates remote execution.

Priority dispatch: merge → QA → impl (one pipeline per project at a time).
Uses PipelineSequencer to drive workers through multi-step command sequences.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from core import events as ev
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.remote_protocol import (
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from orchestrator.registry import WorkerRegistry
from orchestrator.sequencer import PipelineSequencer

logger = logging.getLogger(__name__)


async def dispatch_pending(store: Store, registry: WorkerRegistry) -> int:
    """Discover ready tasks in priority order and dispatch to available workers.

    Priority: merge (ready_for_deployment) → QA (ready_for_qa) → impl (ready_for_implementation).
    Enforces one-pipeline-per-project: skips projects that already have an IN_PROGRESS task.
    Returns count of tasks dispatched in this pass.
    """
    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    spec_manager = SpecManager(store)
    sequencer = PipelineSequencer(store)

    active_projects = await project_manager.list_projects()

    # Candidates by pipeline type: (task, project, spec | None)
    merge_candidates: list[tuple[Task, Project]] = []
    qa_candidates: list[tuple[Task, Project, Spec]] = []
    impl_candidates: list[tuple[Task, Project, Spec]] = []

    from worker.capability_check import effective_capabilities

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

        # Skip project if any task is already IN_PROGRESS
        project_in_progress = False
        for task_id in task_ids_ordered:
            task = await task_manager.get_task(task_id)
            if task is not None and task.status == ev.IN_PROGRESS:
                project_in_progress = True
                break

        if project_in_progress:
            continue

        # Collect candidates for this project
        for task_id in task_ids_ordered:
            task = await task_manager.get_task(task_id)
            if task is None:
                continue

            if task.status == ev.READY_FOR_DEPLOYMENT:
                # Check not already failed auto-merge
                task_events = await store.get_events(task_id, "task")
                if not any(e.event_type == ev.TASK_AUTO_MERGE_FAILED for e in task_events):
                    merge_candidates.append((task, project))

            elif task.status == ev.READY_FOR_QA:
                spec = await spec_manager.get_current_spec(task_id)
                if spec is None:
                    logger.warning("Task %s has no spec, skipping QA dispatch", task_id)
                    continue
                qa_candidates.append((task, project, spec))

            elif task.status == ev.READY_FOR_IMPLEMENTATION:
                spec = await spec_manager.get_current_spec(task_id)
                if spec is None:
                    logger.warning("Task %s has no spec assigned, skipping", task_id)
                    continue
                impl_candidates.append((task, project, spec))

    # Sort each group by task creation time (oldest first)
    merge_candidates.sort(key=lambda c: c[0].created_at)
    qa_candidates.sort(key=lambda c: c[0].created_at)
    impl_candidates.sort(key=lambda c: c[0].created_at)

    count = 0
    dispatched_projects: set[UUID] = set()

    def _start_pipeline(
        pipeline_type: str,
        task_t: Task,
        project_t: Project,
        spec_t: Spec | None,
    ) -> bool:
        """Try to dispatch one pipeline. Returns True if dispatched."""
        nonlocal count
        if project_t.id in dispatched_projects:
            return False
        required = list(effective_capabilities(task_t, project_t))
        worker = registry.find_available(required)
        if worker is None:
            return False

        reservation_id = str(uuid4())
        registry.assign_job(worker.worker_id, reservation_id)
        dispatched_projects.add(project_t.id)

        channel = worker.channel
        if channel is None:
            logger.warning("Worker %s has no channel, skipping", worker.worker_id)
            registry.clear_job(worker.worker_id)
            return False

        if pipeline_type == "merge":
            coro = sequencer.run_merge_pipeline(channel, task_t, project_t)
        elif pipeline_type == "qa":
            assert spec_t is not None
            coro = sequencer.run_qa_pipeline(channel, task_t, project_t, spec_t)
        else:
            assert spec_t is not None
            coro = sequencer.run_impl_pipeline(channel, task_t, project_t, spec_t)

        worker_id = worker.worker_id

        task_id_for_recovery = task_t.id

        async def _run_pipeline(
            _coro: object = coro, _worker_id: str = worker_id
        ) -> None:
            try:
                await _coro  # type: ignore[misc]
            except Exception:
                logger.exception(
                    "Pipeline task failed unexpectedly for worker=%s", _worker_id
                )
                # Recover the task — transition to blocked so it doesn't stay in_progress
                try:
                    state_machine = TaskStateMachine(store)
                    await state_machine.transition(
                        task_id_for_recovery,
                        ev.BLOCKED,
                        extra_payload={"failure_reason": "pipeline crashed unexpectedly"},
                    )
                except Exception:
                    logger.warning(
                        "Failed to recover task %s after pipeline crash",
                        task_id_for_recovery,
                        exc_info=True,
                    )
            finally:
                try:
                    registry.clear_job(_worker_id)
                except Exception:
                    pass

        asyncio.create_task(_run_pipeline())
        count += 1
        logger.info(
            "Dispatched %s pipeline for task=%s to worker=%s",
            pipeline_type,
            task_t.id,
            worker.worker_id,
        )
        return True

    # Process in priority order: merge → QA → impl
    for merge_task, merge_project in merge_candidates:
        _start_pipeline("merge", merge_task, merge_project, None)

    for qa_task, qa_project, qa_spec in qa_candidates:
        _start_pipeline("qa", qa_task, qa_project, qa_spec)

    for impl_task, impl_project, impl_spec in impl_candidates:
        _start_pipeline("impl", impl_task, impl_project, impl_spec)

    return count


async def dispatch_loop(
    store: Store, registry: WorkerRegistry, interval_seconds: float = 2.0
) -> None:
    """Run dispatch_pending + compile_all repeatedly at the given interval."""
    while True:
        try:
            await asyncio.wait_for(dispatch_pending(store, registry), timeout=30)
        except TimeoutError:
            logger.warning("dispatch_pending timed out")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("dispatch_pending failed, will retry", exc_info=True)

        # HLS compilation (runs locally, no worker needed)
        try:
            from core.compiler import compile_all

            count = await compile_all(store)
            if count > 0:
                logger.info("compile_all: compiled %d HLS entries", count)
        except Exception:
            logger.warning("compile_all failed", exc_info=True)

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
