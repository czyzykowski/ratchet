"""Job dispatcher: matches ready tasks to available workers and initiates remote execution.

Priority dispatch: merge → QA → impl (one pipeline per project at a time).
Uses PipelineSequencer to drive workers through multi-step command sequences.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from core import events as ev
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from orchestrator.registry import WorkerRegistry
from orchestrator.sequencer import PipelineSequencer

logger = logging.getLogger(__name__)


async def dispatch_pending(store: Store, registry: WorkerRegistry) -> None:
    """Discover ready tasks in priority order and dispatch to available workers.

    Priority: merge (ready_for_deployment) → impl (ready_for_implementation).
    Enforces one-pipeline-per-project: skips projects that already have an IN_PROGRESS task.
    """
    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    spec_manager = SpecManager(store)
    sequencer = PipelineSequencer(store)

    active_projects = await project_manager.list_projects()

    # Candidates by pipeline type: (task, project, spec | None)
    merge_candidates: list[tuple[Task, Project]] = []
    impl_candidates: list[tuple[Task, Project, Spec]] = []
    resume_candidates: list[tuple[Task, Project, Spec]] = []

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
                # Check deployment mode (only local mode uses auto-merge)
                from core.qa_runner import load_deployment_config

                ratchet_yaml = (
                    project.ratchet_yaml
                    if project.config_source == "db"
                    else None
                )
                deploy_cfg = load_deployment_config(
                    project.local_path, ratchet_yaml
                )
                if deploy_cfg.mode != "local":
                    continue  # PR-based deploys handled by poll_pr_merges

                # Check not already failed auto-merge
                task_events = await store.get_events(task_id, "task")
                if not any(e.event_type == ev.TASK_AUTO_MERGE_FAILED for e in task_events):
                    merge_candidates.append((task, project))

            elif task.status == ev.WAITING_FOR_INPUT:
                # Check if all questions have been answered
                from core import qa_manager

                pending = await qa_manager.get_pending_question(store, task_id)
                if pending is not None:
                    continue  # still waiting for answer
                spec = await spec_manager.get_current_spec(task_id)
                if spec is None:
                    continue
                resume_candidates.append((task, project, spec))

            elif task.status == ev.READY_FOR_IMPLEMENTATION:
                spec = await spec_manager.get_current_spec(task_id)
                if spec is None:
                    logger.warning("Task %s has no spec assigned, skipping", task_id)
                    continue
                # Check dependencies are met
                if task.depends_on:
                    unmet = False
                    for dep_id_str in task.depends_on:
                        try:
                            dep_task = await task_manager.get_task(UUID(dep_id_str))
                        except (ValueError, TypeError):
                            unmet = True
                            break
                        if dep_task is None or dep_task.status != ev.DEPLOYED:
                            unmet = True
                            break
                    if unmet:
                        continue
                impl_candidates.append((task, project, spec))

    # Sort each group by task creation time (oldest first)
    merge_candidates.sort(key=lambda c: c[0].created_at)
    resume_candidates.sort(key=lambda c: c[0].created_at)
    impl_candidates.sort(key=lambda c: c[0].created_at)

    dispatched_projects: set[UUID] = set()
    state_machine = TaskStateMachine(store)

    async def _start_pipeline(
        pipeline_type: str,
        task_t: Task,
        project_t: Project,
        spec_t: Spec | None,
    ) -> bool:
        """Try to dispatch one pipeline. Returns True if dispatched.

        Transitions the task to IN_PROGRESS and records the assignment
        SYNCHRONOUSLY (before creating the async pipeline task) to prevent
        duplicate dispatch on the next dispatch_pending cycle.
        """
        if project_t.id in dispatched_projects:
            return False
        required = list(effective_capabilities(task_t, project_t))
        worker = registry.find_available(required)
        if worker is None:
            return False

        channel = worker.channel
        if channel is None:
            logger.warning("Worker %s has no channel, skipping", worker.worker_id)
            return False

        # Reserve worker FIRST (before any event writes) — if the worker
        # disconnected between find_available and now, this fails fast
        # before we've committed any state changes.
        execution_id = uuid4()
        try:
            registry.assign_job(worker.worker_id, str(execution_id))
        except KeyError:
            logger.warning(
                "Worker %s disconnected before dispatch, skipping",
                worker.worker_id,
            )
            return False

        await store.append_event(
            aggregate_id=task_t.id,
            aggregate_type="task",
            event_type=ev.TASK_ASSIGNED_TO_WORKER,
            payload={
                "worker_id": worker.worker_id,
                "execution_id": str(execution_id),
            },
        )
        # Only transition to IN_PROGRESS for impl tasks (ready_for_implementation
        # or waiting_for_input). Merge tasks stay in their current state —
        # the pipeline sequencer handles their transitions.
        if task_t.status in (ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT):
            await state_machine.transition(task_t.id, ev.IN_PROGRESS)

        dispatched_projects.add(project_t.id)

        if pipeline_type == "merge":
            coro = sequencer.run_merge_pipeline(channel, task_t, project_t)
        elif pipeline_type == "resume":
            assert spec_t is not None
            coro = sequencer.run_resume_pipeline(channel, task_t, project_t, spec_t)
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
                try:
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
        logger.info(
            "Dispatched %s pipeline for task=%s to worker=%s",
            pipeline_type,
            task_t.id,
            worker.worker_id,
        )
        return True

    # Process in priority order: merge → impl
    for merge_task, merge_project in merge_candidates:
        await _start_pipeline("merge", merge_task, merge_project, None)

    # Baseline QA: before dispatching impl tasks, check that the project's
    # develop HEAD passes QA. This prevents wasting execution cycles when
    # develop is broken (e.g. lint/typecheck errors from infrastructure changes).
    from core.event_queries import has_pending_baseline_qa_failure, should_skip_baseline_qa
    from core.qa_runner import check_baseline_qa
    from worker.worktree import (
        create_baseline_worktree,
        remove_qa_worktree,
    )

    # Resume tasks (WAITING_FOR_INPUT with answered questions) — priority over fresh impl
    for resume_task, resume_project, resume_spec in resume_candidates:
        await _start_pipeline("resume", resume_task, resume_project, resume_spec)

    # Impl tasks with baseline QA check
    baseline_failed_projects: set[UUID] = set()
    baseline_passed_projects: set[UUID] = set()
    for impl_task, impl_project, impl_spec in impl_candidates:
        if impl_project.id in baseline_failed_projects:
            continue

        # Skip baseline QA for projects that require capabilities not available
        # locally (e.g. osx projects can't run baseline QA on linux)
        project_caps = set(impl_project.required_capabilities or [])
        if project_caps:
            await _start_pipeline("impl", impl_task, impl_project, impl_spec)
            continue

        # If we already ran baseline for this project in this cycle, reuse result
        if impl_project.id in baseline_passed_projects:
            await _start_pipeline("impl", impl_task, impl_project, impl_spec)
            continue

        # Check if baseline QA was already attempted and is pending/skipped
        task_events = await store.get_events(impl_task.id, "task")
        if has_pending_baseline_qa_failure(task_events):
            continue
        if should_skip_baseline_qa(task_events):
            await _start_pipeline("impl", impl_task, impl_project, impl_spec)
            continue

        # Run baseline QA locally (once per project per dispatch cycle)
        ratchet_yaml = (
            impl_project.ratchet_yaml
            if impl_project.config_source == "db"
            else None
        )
        try:
            baseline_wt = await asyncio.to_thread(
                create_baseline_worktree, impl_project.local_path
            )
        except Exception as exc:
            logger.warning("Baseline QA worktree failed for %s: %s", impl_project.name, exc)
            await _start_pipeline("impl", impl_task, impl_project, impl_spec)
            continue

        try:
            failures = await asyncio.to_thread(
                check_baseline_qa, baseline_wt, ratchet_yaml
            )
        finally:
            await asyncio.to_thread(
                remove_qa_worktree, impl_project.local_path, baseline_wt
            )

        if failures:
            combined = "\n\n".join(f"Step '{r.step_name}':\n{r.output}" for r in failures)
            logger.warning("Baseline QA failed for %s — skipping impl dispatch", impl_project.name)
            baseline_failed_projects.add(impl_project.id)
            if not has_pending_baseline_qa_failure(task_events):
                await store.append_event(
                    aggregate_id=impl_task.id,
                    aggregate_type="task",
                    event_type=ev.TASK_BASELINE_QA_FAILED,
                    payload={"failure_output": combined},
                )
        else:
            baseline_passed_projects.add(impl_project.id)
            await _start_pipeline("impl", impl_task, impl_project, impl_spec)



async def _poll_pr_merges(store: Store) -> None:
    """Poll GitHub for merged PRs and transition tasks to deployed."""
    import json as _json
    import os
    import subprocess as _subprocess
    from pathlib import Path

    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    state_machine = TaskStateMachine(store)

    def _run_gh(args: list[str], cwd: str) -> _subprocess.CompletedProcess[str]:
        if (Path(cwd) / "flake.nix").exists():
            return _subprocess.run(
                ["nix", "develop", "--command", "gh"] + args,
                cwd=cwd, capture_output=True, text=True,
            )
        return _subprocess.run(
            ["gh"] + args, cwd=cwd, capture_output=True, text=True,
        )

    for project in await project_manager.list_projects():
        tasks = await task_manager.list_tasks_by_project(project.id)
        for task in tasks:
            if task.status != ev.READY_FOR_DEPLOYMENT:
                continue
            task_events = await store.get_events(task.id, "task")
            pr_number: int | None = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_PR_CREATED:
                    pr_number = event.payload.get("pr_number")
                    break
            if pr_number is None:
                continue

            cwd = project.local_path or os.getcwd()
            _pr_num = int(pr_number)
            _cwd = str(cwd)
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _run_gh(
                    ["pr", "view", str(_pr_num), "--json", "state,mergeCommit"],
                    _cwd,
                ),
            )
            if proc.returncode != 0:
                continue

            try:
                state_data = _json.loads(proc.stdout)
                pr_state = state_data.get("state", "")
            except Exception:
                continue

            if pr_state == "MERGED":
                logger.info("PR %s merged — deploying task=%s", pr_number, task.id)
                sha = (state_data.get("mergeCommit") or {}).get("oid")
                extra = {"merge_commit_sha": sha} if sha else None
                await state_machine.transition(
                    task.id, ev.DEPLOYED, extra_payload=extra
                )


async def _recover_orphaned_tasks(store: Store, registry: WorkerRegistry) -> None:
    """Reset in_progress tasks with no active worker back to ready_for_implementation.

    Called once at dispatch loop startup to recover from orchestrator restarts.
    """
    task_manager = TaskManager(store)
    project_manager = ProjectManager(store)
    state_machine = TaskStateMachine(store)
    active_worker_execs = {
        w.current_execution_id for w in registry.all_workers() if w.current_execution_id
    }

    for project in await project_manager.list_projects():
        tasks = await task_manager.list_tasks_by_project(project.id)
        for task in tasks:
            if task.status != ev.IN_PROGRESS:
                continue
            # Check if any worker is actively working on this task
            task_events = await store.get_events(task.id, "task")
            assigned_exec = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_ASSIGNED_TO_WORKER:
                    assigned_exec = event.payload.get("execution_id")
                    break
            if assigned_exec and assigned_exec in active_worker_execs:
                continue  # worker is still working on it
            logger.info(
                "Recovering orphaned in_progress task=%s project=%s",
                task.id, project.name,
            )
            await state_machine.transition(
                task.id, ev.READY_FOR_IMPLEMENTATION,
                extra_payload={"reason": "orchestrator_restart_recovery"},
            )


async def _reap_orphaned_executions(
    store: Store,
    registry: WorkerRegistry,
    timeout_hours: float = 2.0,
) -> int:
    """Find running executions older than timeout with no connected worker and mark them failed.

    Returns count of reaped executions.
    """
    from core.execution_manager import _build_execution
    from core.project_manager import ProjectManager
    from core.task_manager import TaskManager

    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)

    active_worker_execs = {
        w.current_execution_id for w in registry.all_workers() if w.current_execution_id
    }

    threshold = datetime.now(UTC) - timedelta(hours=timeout_hours)
    reaped = 0

    for project in await project_manager.list_projects():
        tasks = await task_manager.list_tasks_by_project(project.id)
        for task in tasks:
            if task.status in (ev.DEPLOYED, ev.ABANDONED):
                continue

            exec_events = await store.get_events(task.id, "task_executions")
            execution_ids: list[UUID] = []
            seen: set[UUID] = set()
            for event in exec_events:
                eid_str = event.payload.get("execution_id")
                if eid_str:
                    eid = UUID(eid_str)
                    if eid not in seen:
                        seen.add(eid)
                        execution_ids.append(eid)

            for execution_id in execution_ids:
                execution_events = await store.get_events(execution_id, "execution")
                execution = _build_execution(execution_events)
                if execution is None or execution.status != "running":
                    continue
                if execution.started_at >= threshold:
                    continue
                if str(execution_id) in active_worker_execs:
                    continue

                age = datetime.now(UTC) - execution.started_at
                logger.warning(
                    "Reaped orphaned execution %s for task %s (started %s ago)",
                    execution_id,
                    task.id,
                    age,
                )
                await store.append_event(
                    aggregate_id=execution_id,
                    aggregate_type="execution",
                    event_type=ev.EXECUTION_FAILED,
                    payload={
                        "execution_id": str(execution_id),
                        "status": "failed",
                        "failure_reason": "execution timed out (worker disconnected)",
                    },
                )
                reaped += 1

    if reaped > 0:
        await store.refresh_views()

    return reaped


async def dispatch_loop(
    store: Store,
    registry: WorkerRegistry,
    interval_seconds: float = 2.0,
    notification_queue: asyncio.Queue[str] | None = None,
) -> None:
    """Run dispatch_pending + compile_all, triggered by notifications or polling.

    If notification_queue is provided, the loop wakes on LISTEN/NOTIFY events
    from Postgres instead of sleeping for interval_seconds. Falls back to
    polling if no notification arrives within interval_seconds.
    """
    # Startup: recover tasks orphaned by previous orchestrator crash
    try:
        await _recover_orphaned_tasks(store, registry)
    except Exception:
        logger.warning("Orphan recovery failed", exc_info=True)

    # Startup: clean up any executions left in 'running' status from before restart.
    # Uses timeout_hours=0 so ALL running executions with no connected worker are reaped.
    try:
        count = await _reap_orphaned_executions(store, registry, timeout_hours=0)
        if count > 0:
            logger.info("Startup: reaped %d orphaned executions", count)
    except Exception:
        logger.warning("Startup execution cleanup failed", exc_info=True)

    import time

    _COMPILE_INTERVAL = 60  # seconds between HLS compilation runs
    _RECOVERY_INTERVAL = 120  # seconds between orphan recovery runs
    _REAP_INTERVAL = 300  # seconds between execution reaper runs
    _PR_POLL_INTERVAL = 300  # seconds between PR merge polls
    last_compile = 0.0
    last_recovery = time.monotonic()
    last_reap = 0.0
    last_pr_poll = 0.0

    while True:
        # Periodic orphan recovery — catch tasks whose pipeline crashed
        now = time.monotonic()
        if now - last_recovery >= _RECOVERY_INTERVAL:
            last_recovery = now
            try:
                await _recover_orphaned_tasks(store, registry)
            except Exception:
                logger.warning("Periodic orphan recovery failed", exc_info=True)

        # Periodic execution reaper — clean up orphaned running executions
        if now - last_reap >= _REAP_INTERVAL:
            last_reap = now
            try:
                count = await _reap_orphaned_executions(store, registry)
                if count > 0:
                    logger.info("Reaped %d orphaned executions", count)
            except Exception:
                logger.warning("Execution reaper failed", exc_info=True)

        try:
            await asyncio.wait_for(dispatch_pending(store, registry), timeout=120)
        except TimeoutError:
            logger.warning("dispatch_pending timed out")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("dispatch_pending failed, will retry", exc_info=True)

        # HLS compilation — throttled to once per minute
        now = time.monotonic()
        if now - last_compile >= _COMPILE_INTERVAL:
            last_compile = now
            try:
                from core.compiler import compile_all

                count = await compile_all(store)
                if count > 0:
                    logger.info("compile_all: compiled %d HLS entries", count)
            except Exception:
                logger.warning("compile_all failed", exc_info=True)

        # PR merge polling — check if any PR-mode tasks had their PR merged
        now = time.monotonic()
        if now - last_pr_poll >= _PR_POLL_INTERVAL:
            last_pr_poll = now
            try:
                await _poll_pr_merges(store)
            except Exception:
                logger.warning("poll_pr_merges failed", exc_info=True)

        # Wait for notification or fall back to polling interval
        if notification_queue is not None:
            try:
                await asyncio.wait_for(
                    notification_queue.get(), timeout=interval_seconds
                )
            except TimeoutError:
                pass  # polling fallback
        else:
            await asyncio.sleep(interval_seconds)

