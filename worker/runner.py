"""Worker runner: single-pass task execution connecting all core components."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess as _subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from core import events as ev
from core import qa_manager
from core.context_assembler import ContextAssembler, ContextAssemblyError, ExecutionContext
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.models import Project, Spec, Task
from core.models_config import WORKER_MODEL
from core.project_manager import ProjectManager
from core.qa_runner import (
    build_review_prompt,
    check_baseline_qa,
    get_git_diff,
    load_qa_config,
    parse_review_output,
    run_qa_steps,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from worker.listener import NotificationListener

logger = logging.getLogger(__name__)



async def recover_orphaned_tasks(store: Store) -> int:
    """At startup, reset in_progress tasks to ready_for_implementation.

    Orphans are tasks left stuck in_progress by a previous worker crash or restart.
    Returns the count of tasks reset.
    """
    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    state_machine = TaskStateMachine(store)

    active_projects = await project_manager.list_projects()
    reset_count = 0

    for project in active_projects:
        project_task_events = await store.get_events(project.id, "project_tasks")
        task_ids_seen: set[UUID] = set()
        for event in project_task_events:
            tid_str = event.payload.get("task_id")
            if not tid_str:
                continue
            tid = UUID(tid_str)
            if tid in task_ids_seen:
                continue
            task_ids_seen.add(tid)
            task = await task_manager.get_task(tid)
            if task is not None and task.status == ev.IN_PROGRESS:
                logger.warning(
                    "Orphaned in_progress task at startup: task=%s project=%s"
                    " — resetting to ready_for_implementation",
                    tid,
                    project.name,
                )
                await state_machine.transition(
                    tid,
                    ev.READY_FOR_IMPLEMENTATION,
                    extra_payload={"reason": "worker restart: orphan recovery"},
                )
                reset_count += 1

    if reset_count:
        logger.info("Orphan recovery: reset %d task(s) to ready_for_implementation", reset_count)
    return reset_count


async def get_next_task(
    store: Store,
    project_manager: ProjectManager,
    spec_manager: SpecManager,
    state_machine: TaskStateMachine,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
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
    if project_id is not None:
        active_projects = [p for p in active_projects if p.id == project_id]
    task_manager = TaskManager(store)
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

        # Skip this project entirely if it already has a task in progress.
        has_in_progress = False
        project_tasks: list[tuple[UUID, Any]] = []
        for task_id in task_ids_ordered:
            task = await task_manager.get_task(task_id)
            if task is not None and task.status == ev.IN_PROGRESS:
                has_in_progress = True
                break
            project_tasks.append((task_id, task))
        if has_in_progress:
            continue

        for task_id, task in project_tasks:
            actionable = (ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT)
            if task is None or task.status not in actionable:
                continue

            if task.status == ev.WAITING_FOR_INPUT:
                pending = await qa_manager.get_pending_question(store, task_id)
                if pending is not None:
                    continue

            # Skip if any dependency is not yet deployed
            if task.depends_on:
                unmet = False
                for dep_id_str in task.depends_on:
                    try:
                        dep_id = UUID(dep_id_str)
                    except ValueError:
                        logger.debug(
                            "Task %s has invalid dep UUID %s, skipping",
                            task_id,
                            dep_id_str,
                        )
                        unmet = True
                        break
                    dep_task = await task_manager.get_task(dep_id)
                    if dep_task is None or dep_task.status != ev.DEPLOYED:
                        logger.debug("Task %s skipped: dep %s not deployed", task_id, dep_id_str)
                        unmet = True
                        break
                if unmet:
                    continue

            if not set(task.required_capabilities).issubset(set(local_capabilities)):
                logger.debug(
                    "Task %s skipped: required_capabilities %s not met by local %s",
                    task_id,
                    task.required_capabilities,
                    local_capabilities,
                )
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


def _has_pending_baseline_qa_failure(task_events: list[Any]) -> bool:
    """True if the most recent baseline QA failure has no retry or force-execute after it."""
    last_failed_seq: int | None = None
    last_cleared_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type in (ev.TASK_BASELINE_QA_RETRY, ev.TASK_FORCE_EXECUTE):
            last_cleared_seq = max(last_cleared_seq or 0, event.sequence)
    if last_failed_seq is None:
        return False
    return last_cleared_seq is None or last_failed_seq > last_cleared_seq


def _should_skip_baseline_qa(task_events: list[Any]) -> bool:
    """True if force-execute (not mere retry) was requested after the last baseline QA failure."""
    last_failed_seq: int | None = None
    last_force_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type == ev.TASK_FORCE_EXECUTE:
            last_force_seq = event.sequence
    if last_force_seq is None:
        return False
    return last_failed_seq is None or last_force_seq > last_failed_seq


async def run_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> bool:
    """Single-pass task execution.

    invoker parameter allows injection for testing — defaults to ClaudeCodeInvoker().
    Returns True if a task was found and processed, False otherwise.
    """
    if invoker is None:
        invoker = ClaudeCodeInvoker()

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(
        store, project_manager, spec_manager, state_machine,
        local_capabilities, project_id=project_id,
    )
    if result is None:
        logger.info("No tasks ready for implementation.")
        return False

    task, project, spec = result

    # Resume path for waiting_for_input tasks
    if task.status == ev.WAITING_FOR_INPUT:
        execution_manager = ExecutionManager(store, project.local_path)
        context_assembler = ContextAssembler(store)
        execution = await execution_manager.get_current_execution(task.id)
        if execution is None:
            logger.error(
                "No running execution for waiting_for_input task=%s, blocking", task.id
            )
            await state_machine.transition(
                task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
            )
            await state_machine.transition(task.id, ev.BLOCKED)
            return True
        execution_id = execution.id
        await state_machine.transition(
            task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
        )
        logger.info("Resuming execution after input: task=%s execution=%s", task.id, execution_id)
        try:
            context = await context_assembler.assemble(execution_id, project)
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
            return True
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
        return True

    # Check if force-execute was requested after last baseline failure
    task_events = await store.get_events(task.id, "task")
    skip_baseline = _should_skip_baseline_qa(task_events)

    if not skip_baseline:
        ratchet_yaml = project.ratchet_yaml if project.config_source == "db" else None
        try:
            baseline_worktree = _create_baseline_worktree(project.local_path)
        except QAWorktreeError as exc:
            logger.warning(
                "Baseline QA worktree creation failed for project=%s: %s — skipping baseline check",
                project.name,
                exc,
            )
            baseline_failures = []
        else:
            try:
                baseline_failures = check_baseline_qa(baseline_worktree, ratchet_yaml)
            finally:
                _remove_qa_worktree(project.local_path, baseline_worktree)
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
            # Only emit event if not already pending (avoid spam on repeated polls)
            if not _has_pending_baseline_qa_failure(task_events):
                await store.append_event(
                    aggregate_id=task.id,
                    aggregate_type="task",
                    event_type=ev.TASK_BASELINE_QA_FAILED,
                    payload={"failure_output": combined},
                )
            return False
    execution_manager = ExecutionManager(store, project.local_path)
    context_assembler = ContextAssembler(store)

    logger.info(
        "Starting execution: task=%s project=%s spec=%s",
        task.id,
        project.name,
        spec.id,
    )

    await state_machine.transition(task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    try:
        execution = await execution_manager.start_execution(task.id, spec.id, project)
    except OSError as exc:
        failure_reason = str(exc)
        logger.error(
            "Environment preparation failed: task=%s reason=%s", task.id, failure_reason
        )
        await state_machine.transition(task.id, ev.BLOCKED)
        return True

    execution_id = execution.id
    logger.info("Execution started: task=%s execution=%s", task.id, execution_id)

    try:
        context = await context_assembler.assemble(execution_id, project)
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
        return True

    if os.environ.get('RATCHET_DEBUG') == '1':
        print(f'[DEBUG] Assembled prompt ({len(context.prompt)} chars):')
        print(context.prompt[:2000])  # first 2000 chars to avoid overwhelming output
        print(f'[DEBUG] Worktree: {context.worktree_path}')
        print('[DEBUG] Command: claude -p <prompt> --allowedTools Bash,Read,Write,Edit,Glob,Grep')

    try:
        invocation_result = await asyncio.to_thread(invoker.invoke, context)
    except Exception as exc:
        failure_reason = f"unexpected error: {exc}"
        logger.error("Unexpected error: task=%s error=%s", task.id, str(exc))
        await execution_manager.fail_execution(execution_id, failure_reason)
        await state_machine.transition(task.id, ev.BLOCKED)
        raise

    if os.environ.get('RATCHET_DEBUG') == '1':
        print(f'[DEBUG] Invocation status: {invocation_result.status}')
        print(f'[DEBUG] Trace path: {invocation_result.trace_path}')
        print('[DEBUG] Raw output preview:')
        try:
            trace_content = Path(invocation_result.trace_path).read_text()[:1000]
            print(trace_content)
        except Exception:
            print('[DEBUG] Could not read trace file')

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

    return True


async def get_next_qa_task(
    store: Store,
    project_manager: ProjectManager,
    spec_manager: SpecManager,
    state_machine: TaskStateMachine,
    project_id: UUID | None = None,
) -> tuple[Task, Project, Spec] | None:
    """Find oldest ready_for_qa task with active project and assigned spec.

    Returns (task, project, spec) tuple or None if nothing ready.
    """
    active_projects = await project_manager.list_projects()
    if project_id is not None:
        active_projects = [p for p in active_projects if p.id == project_id]
    task_manager = TaskManager(store)
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
            if task is None or task.status != ev.READY_FOR_QA:
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


class QAWorktreeError(Exception):
    """Raised when a QA worktree cannot be created."""


def _find_existing_worktree(project_path: str, branch: str) -> str | None:
    """Return the path of an existing worktree checked out on branch, or None."""
    result = _subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("branch ") and current_path:
            # git reports branch as refs/heads/<name>
            reported = line[len("branch "):]
            if reported == f"refs/heads/{branch}" or reported == branch:
                return current_path
    return None


def _create_baseline_worktree(project_path: str) -> str:
    """Create a temporary worktree on HEAD for baseline QA.

    Runs detached from HEAD so it reflects the current clean branch state.
    Symlinks .venv and web/spa/node_modules from the project root so QA steps work.
    Returns the worktree path. Caller must clean up via _remove_qa_worktree().
    Raises QAWorktreeError if worktree creation fails.
    """
    import uuid as _uuid

    baseline_id = str(_uuid.uuid4())[:8]
    wt_path = os.path.join(project_path, ".worktrees", f"baseline-{baseline_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", "--detach", wt_path],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise QAWorktreeError(
            f"git worktree add (baseline) failed: {result.stderr.strip()}"
        )
    # Symlink .venv so Python QA steps work
    venv_src = os.path.join(project_path, ".venv")
    venv_dst = os.path.join(wt_path, ".venv")
    if os.path.exists(venv_src) and not os.path.lexists(venv_dst):
        os.symlink(venv_src, venv_dst)
    # Symlink root node_modules so npm/deno npm packages work
    nm_src = os.path.join(project_path, "node_modules")
    nm_dst = os.path.join(wt_path, "node_modules")
    if os.path.exists(nm_src) and not os.path.lexists(nm_dst):
        os.symlink(nm_src, nm_dst)
    # Symlink web/spa/node_modules so npm build steps work
    spa_nm_src = os.path.join(project_path, "web", "spa", "node_modules")
    spa_nm_dst = os.path.join(wt_path, "web", "spa", "node_modules")
    if os.path.exists(spa_nm_src) and not os.path.lexists(spa_nm_dst):
        os.symlink(spa_nm_src, spa_nm_dst)
    return wt_path


def _create_qa_worktree(project_path: str, execution_branch: str) -> tuple[str, bool]:
    """Create a temporary worktree on the execution branch for QA testing.

    Symlinks .venv from the project root so relative .venv/bin/python commands work.
    Returns (worktree_path, owned) where owned=True means we created it and must
    remove it afterwards. owned=False means we reused an existing worktree (e.g. the
    execution worktree that was never cleaned up) and must NOT remove it.
    Raises QAWorktreeError if worktree creation fails and no existing worktree found.
    """
    import uuid as _uuid

    qa_id = str(_uuid.uuid4())[:8]
    qa_path = os.path.join(project_path, ".worktrees", f"qa-{qa_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", qa_path, execution_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Branch may already be checked out in the execution worktree — reuse it.
        existing = _find_existing_worktree(project_path, execution_branch)
        if existing:
            logger.info(
                "Reusing existing worktree at %s for branch %s",
                existing,
                execution_branch,
            )
            return existing, False
        raise QAWorktreeError(
            f"git worktree add failed for branch {execution_branch!r}:"
            f" {result.stderr.strip()}"
        )
    venv_src = os.path.join(project_path, ".venv")
    venv_dst = os.path.join(qa_path, ".venv")
    if os.path.exists(venv_src) and not os.path.lexists(venv_dst):
        os.symlink(venv_src, venv_dst)
    # Symlink web/spa/node_modules so npm build steps work
    spa_nm_src = os.path.join(project_path, "web", "spa", "node_modules")
    spa_nm_dst = os.path.join(qa_path, "web", "spa", "node_modules")
    if os.path.exists(spa_nm_src) and not os.path.lexists(spa_nm_dst):
        os.symlink(spa_nm_src, spa_nm_dst)
    return qa_path, True


def _remove_qa_worktree(project_path: str, qa_path: str) -> None:
    """Remove a temporary QA worktree."""
    try:
        _subprocess.run(
            ["git", "worktree", "remove", "--force", qa_path],
            cwd=project_path,
            check=False,
            capture_output=True,
        )
    except Exception:
        logger.warning("Failed to remove QA worktree at %s", qa_path)


def _get_qa_fix_attempts(task_events: list[Any]) -> int:
    """Read qa_fix_attempts from the latest TASK_STATUS_CHANGED event payload (default 0)."""
    attempts = 0
    for event in reversed(task_events):
        if event.event_type == ev.TASK_STATUS_CHANGED:
            val = event.payload.get("qa_fix_attempts")
            if val is not None:
                attempts = int(val)
                break
    return attempts


async def run_qa_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None,
    project_id: UUID | None = None,
) -> bool:
    """Single-pass QA execution.

    invoker parameter allows injection for testing — defaults to ClaudeCodeInvoker().
    Returns True if a QA task was found and processed, False otherwise.
    """
    if invoker is None:
        invoker = ClaudeCodeInvoker()

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    # Step 1: find next QA task
    result = await get_next_qa_task(
        store, project_manager, spec_manager, state_machine, project_id=project_id
    )
    if result is None:
        logger.info("No QA tasks ready.")
        return False

    task, project, spec = result

    # Step 2: load QA config
    qa_ratchet_yaml = project.ratchet_yaml if project.config_source == "db" else None
    config = load_qa_config(project.local_path, qa_ratchet_yaml)
    if config is None:
        logger.info(
            "No QA config found for task=%s, transitioning to ready_for_merge", task.id
        )
        await state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
        return True

    # Step 3: look up execution branch — required, block task if missing
    execution_events = await store.get_events(task.id, "task_executions")
    execution_branch: str | None = None
    for event in reversed(execution_events):
        if event.event_type == ev.EXECUTION_STARTED:
            bn = event.payload.get("branch_name")
            if bn:
                execution_branch = bn
            break

    if not execution_branch:
        failure_reason = "QA cannot run: no execution branch found for task"
        logger.error("task=%s: %s", task.id, failure_reason)
        await state_machine.transition(
            task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
        )
        return True

    try:
        qa_path, qa_worktree_owned = _create_qa_worktree(project.local_path, execution_branch)
    except QAWorktreeError as exc:
        failure_reason = f"QA worktree creation failed: {exc}"
        logger.error("task=%s: %s", task.id, failure_reason)
        await state_machine.transition(
            task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
        )
        return True

    try:
        step_results = run_qa_steps(config, qa_path)
    finally:
        if qa_worktree_owned:
            _remove_qa_worktree(project.local_path, qa_path)

    failed_steps = [r for r in step_results if r.returncode != 0]

    if failed_steps:
        # Read current qa_fix_attempts
        task_events = await store.get_events(task.id, "task")
        qa_fix_attempts = _get_qa_fix_attempts(task_events)

        if qa_fix_attempts >= config.max_fix_attempts:
            combined_output = "\n\n".join(
                f"Step '{r.step_name}':\n{r.output}" for r in failed_steps
            )
            logger.info(
                "Max fix attempts reached for task=%s, transitioning to blocked", task.id
            )
            await state_machine.transition(
                task.id,
                ev.BLOCKED,
                extra_payload={
                    "failure_reason": combined_output,
                    "qa_fix_attempts": qa_fix_attempts,
                },
            )
            return True

        # Build auto-fix prompt and invoke Claude Code
        failed_output = "\n\n".join(
            f"Step '{r.step_name}' (exit {r.returncode}):\n{r.output}" for r in failed_steps
        )
        fix_prompt = (
            f"{spec.content}\n\n"
            f"QA tools found errors after implementation was marked complete:\n{failed_output}"
        )
        fix_context = ExecutionContext(
            execution_id=uuid4(),
            task_id=task.id,
            spec_id=spec.id,
            worktree_path=project.local_path,
            prompt=fix_prompt,
        )
        logger.info(
            "Auto-fix attempt %d/%d for task=%s",
            qa_fix_attempts + 1, config.max_fix_attempts, task.id
        )
        await asyncio.to_thread(invoker.invoke, fix_context)
        await state_machine.transition(
            task.id,
            ev.READY_FOR_QA,
            extra_payload={"qa_fix_attempts": qa_fix_attempts + 1},
        )
        return True

    # Step 4: all steps pass — run Claude review
    diff = get_git_diff(project.local_path, execution_branch)
    review_prompt = build_review_prompt(spec.content, diff, step_results)

    review_proc = _subprocess.run(
        ["claude", "-p", "--model", WORKER_MODEL, "--allowedTools", "Bash,Read,Glob,Grep"],
        input=review_prompt,
        cwd=project.local_path,
        capture_output=True,
        text=True,
    )
    review_output = review_proc.stdout + review_proc.stderr

    # Step 5: parse review output
    review_result = parse_review_output(review_output)

    if review_result.verdict == "passed":
        logger.info("QA review passed for task=%s", task.id)
        await state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
    else:
        logger.info("QA review failed for task=%s", task.id)
        await state_machine.transition(
            task.id,
            ev.BLOCKED,
            extra_payload={"failure_reason": review_result.full_output},
        )

    return True


def _gh_command(args: list[str], project_local_path: str) -> _subprocess.CompletedProcess[str]:
    """Run a gh command, wrapped in nix develop if flake.nix is present."""
    if (Path(project_local_path) / "flake.nix").exists():
        return _subprocess.run(
            ["nix", "develop", "--command", "gh"] + args,
            cwd=project_local_path,
            capture_output=True,
            text=True,
        )
    return _subprocess.run(
        ["gh"] + args,
        cwd=project_local_path,
        capture_output=True,
        text=True,
    )


async def poll_pr_merges(store: Store, project_local_path: str) -> None:
    """Poll GitHub for merged PRs and transition tasks to deployed.

    Finds all tasks in ready_for_deployment that have a TASK_PR_CREATED event,
    checks each PR's state via gh CLI, and transitions merged PRs to deployed.
    """
    project_manager = ProjectManager(store)
    task_manager = TaskManager(store)
    state_machine = TaskStateMachine(store)

    active_projects = await project_manager.list_projects()

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

        cwd = project.local_path if project.local_path else project_local_path

        for task_id in task_ids_ordered:
            task = await task_manager.get_task(task_id)
            if task is None or task.status != ev.READY_FOR_DEPLOYMENT:
                continue

            task_events = await store.get_events(task_id, "task")
            pr_number: int | None = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_PR_CREATED:
                    pr_number = event.payload.get("pr_number")
                    break

            if pr_number is None:
                continue

            _pr_num = int(pr_number)
            _cwd = str(cwd)
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _gh_command(
                    ["pr", "view", str(_pr_num), "--json", "state,mergeCommit"], _cwd
                ),
            )
            if proc.returncode != 0:
                logger.warning(
                    "gh pr view failed for task=%s pr=%s: %s",
                    task_id,
                    pr_number,
                    proc.stderr.strip(),
                )
                continue

            try:
                import json as _json
                state_data = _json.loads(proc.stdout)
                pr_state = state_data.get("state", "")
            except Exception:
                logger.warning("Failed to parse gh pr view output for task=%s", task_id)
                continue

            if pr_state == "MERGED":
                logger.info("PR %s merged — transitioning task=%s to deployed", pr_number, task_id)
                sha = (state_data.get("mergeCommit") or {}).get("oid") or None
                extra_payload = {"merge_commit_sha": sha} if sha else None
                await state_machine.transition(task_id, ev.DEPLOYED, extra_payload=extra_payload)


async def compile_once(store: Store) -> bool:
    """Single-pass HLS compilation.

    Calls core.compiler.compile_all(store) to iterate all active projects and
    features and compile eligible HLS entries.
    Returns True if any HLS was compiled, False otherwise.
    """
    from core.compiler import compile_all

    count = await compile_all(store)
    if count > 0:
        logger.info("compile_once: compiled %d HLS entries", count)
        return True
    logger.info("compile_once: no eligible HLS entries found")
    return False


async def notification_loop(
    store: Store,
    invoker: ClaudeCodeInvoker,
    dsn: str,
    max_workers: int = 1,
    local_capabilities: list[str] = [],
    _initial_busy_projects: set[UUID] | None = None,
) -> None:
    """React to Postgres LISTEN/NOTIFY events for task status changes and compilation triggers.

    1. Runs startup catchup by calling compile_once, run_once, and run_qa_once before listening.
    2. Enters the notification-driven loop.
    3. Queues notifications received during execution using asyncio.Queue.
    4. Processes queued items after each task completes.
    5. Compilation trigger events route to compile_once only.
    6. Task status events route to run_once + run_qa_once per active project concurrently.

    Each active project gets its own dispatch slot; busy_projects prevents concurrent
    dispatch for the same project within the same worker process.
    """
    queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
    active = False
    if _initial_busy_projects is not None:
        busy_projects: set[UUID] = set(_initial_busy_projects)
    else:
        busy_projects = set()

    async def _dispatch_for_project(pid: UUID) -> None:
        """Run one QA→impl pass for a single project, then release busy lock."""
        try:
            did_qa = await run_qa_once(store, invoker, project_id=pid)
            if not did_qa:
                await run_once(store, invoker, local_capabilities, project_id=pid)
        finally:
            busy_projects.discard(pid)

    async def _dispatch_all() -> None:
        """Dispatch one pass per non-busy active project, then compile once."""
        pm = ProjectManager(store)
        projects = await pm.list_projects()
        to_dispatch = [p.id for p in projects if p.id not in busy_projects]
        for pid in to_dispatch:
            busy_projects.add(pid)
        try:
            if to_dispatch:
                await asyncio.gather(
                    *[_dispatch_for_project(pid) for pid in to_dispatch],
                    return_exceptions=True,
                )
        finally:
            await compile_once(store)

    async def _heartbeat_producer() -> None:
        while True:
            await asyncio.sleep(600)
            logger.info("Worker: heartbeat — queuing catchup pass")
            await queue.put(("heartbeat", "", ""))

    async def _pr_poll_loop() -> None:
        while True:
            await asyncio.sleep(300)
            try:
                await poll_pr_merges(store, os.getcwd())
            except Exception:
                logger.warning("poll_pr_merges failed", exc_info=True)

    async def _notification_producer(listener: NotificationListener) -> None:
        async for event_tuple in listener.listen():
            await queue.put(event_tuple)

    async def _run_loop() -> None:
        nonlocal active
        # Startup orphan recovery — must run before dispatch
        await recover_orphaned_tasks(store)
        # Startup catchup
        logger.info("Worker: running startup catchup")
        await _dispatch_all()
        try:
            await store.refresh_views()
        except Exception:
            logger.warning("View refresh failed after startup catchup", exc_info=True)

        while True:
            event_tuple = await queue.get()
            kind = event_tuple[0]
            if kind == "compile":
                reason = event_tuple[1]
                logger.info("Worker: dequeued compilation trigger reason=%s", reason)
            elif kind == "heartbeat":
                logger.info("Worker: processing heartbeat catchup pass")
            else:
                task_id, status = event_tuple[1], event_tuple[2]
                logger.info(
                    "Worker: dequeued notification task=%s status=%s", task_id, status
                )
            active = True
            try:
                await _dispatch_all()
            finally:
                active = False
                queue.task_done()
                try:
                    await store.refresh_views()
                except Exception:
                    logger.warning("View refresh failed after notification", exc_info=True)

    async with NotificationListener(dsn, max_workers=max_workers) as listener:
        producer_task = asyncio.create_task(_notification_producer(listener))
        heartbeat_task = asyncio.create_task(_heartbeat_producer())
        consumer_task = asyncio.create_task(_run_loop())
        pr_poll_task = asyncio.create_task(_pr_poll_loop())
        try:
            done, pending = await asyncio.wait(
                [producer_task, heartbeat_task, consumer_task, pr_poll_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            pending = {producer_task, consumer_task, pr_poll_task}
            done = set()
        finally:
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        for task in done:
            if not task.cancelled():
                try:
                    exc = task.exception()
                except BaseException:
                    exc = None
                if exc is not None:
                    raise exc


def main(watchdog_timeout: int = 300, local_capabilities: list[str] = []) -> None:
    """Initialize all components with PostgresStore and run once."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(
        _main_async(
            watchdog_timeout=watchdog_timeout,
            local_capabilities=local_capabilities,
        )
    )


async def _main_async(
    watchdog_timeout: int = 300,
    local_capabilities: list[str] = [],
) -> None:
    from core.db import close_pool
    from core.store import PostgresStore

    store = PostgresStore()
    invoker = ClaudeCodeInvoker(watchdog_timeout=watchdog_timeout)
    try:
        did_qa = await run_qa_once(store, invoker)
        if not did_qa:
            did_impl = await run_once(store, invoker, local_capabilities)
            if not did_impl:
                await compile_once(store)
    finally:
        await close_pool()


async def _main_loop_async(
    watchdog_timeout: int = 300,
    local_capabilities: list[str] = [],
) -> None:
    import os
    import signal

    from core.db import close_pool
    from core.store import PostgresStore

    dsn = os.environ["DATABASE_URL"]
    store = PostgresStore()
    invoker = ClaudeCodeInvoker(watchdog_timeout=watchdog_timeout)

    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()

    def _handle_sigint() -> None:
        invoker.terminate()
        if current_task:
            current_task.cancel()

    loop.add_signal_handler(signal.SIGINT, _handle_sigint)
    try:
        await notification_loop(
            store, invoker, dsn, local_capabilities=local_capabilities
        )
    except asyncio.CancelledError:
        logger.info("Worker stopped.")
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        await close_pool()


def main_loop_entry(
    watchdog_timeout: int = 300, local_capabilities: list[str] = []
) -> None:
    """Initialize all components with PostgresStore and run the continuous loop."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(
        _main_loop_async(
            watchdog_timeout=watchdog_timeout,
            local_capabilities=local_capabilities,
        )
    )
