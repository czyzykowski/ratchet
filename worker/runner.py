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
from core.context_assembler import ContextAssembler, ContextAssemblyError, ExecutionContext
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.models import Project, Spec, Task
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

        for task_id in task_ids_ordered:
            task = await task_manager.get_task(task_id)
            if task is None or task.status != ev.READY_FOR_IMPLEMENTATION:
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

    result = await get_next_task(store, project_manager, spec_manager, state_machine)
    if result is None:
        logger.info("No tasks ready for implementation.")
        return False

    task, project, spec = result

    # Check if force-execute was requested after last baseline failure
    task_events = await store.get_events(task.id, "task")
    skip_baseline = _should_skip_baseline_qa(task_events)

    if not skip_baseline:
        baseline_failures = check_baseline_qa(project.local_path)
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
        execution = await execution_manager.start_execution(task.id, spec.id)
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
) -> tuple[Task, Project, Spec] | None:
    """Find oldest ready_for_qa task with active project and assigned spec.

    Returns (task, project, spec) tuple or None if nothing ready.
    """
    active_projects = await project_manager.list_projects()
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


def _create_qa_worktree(
    project_path: str, execution_branch: str | None
) -> tuple[str, str | None]:
    """Create a temporary worktree on the execution branch for QA testing.

    Symlinks .venv from the project root so relative .venv/bin/python commands work.
    Returns (cwd, worktree_path): cwd is where QA steps should run,
    worktree_path is the path to remove afterwards (None if no worktree was created).
    Falls back to (project_path, None) on any error.
    """
    if not execution_branch:
        return project_path, None

    import uuid as _uuid

    qa_id = str(_uuid.uuid4())[:8]
    qa_path = os.path.join(project_path, ".worktrees", f"qa-{qa_id}")
    try:
        _subprocess.run(
            ["git", "worktree", "add", qa_path, execution_branch],
            cwd=project_path,
            check=True,
            capture_output=True,
        )
        venv_src = os.path.join(project_path, ".venv")
        venv_dst = os.path.join(qa_path, ".venv")
        if os.path.exists(venv_src) and not os.path.lexists(venv_dst):
            os.symlink(venv_src, venv_dst)
        return qa_path, qa_path
    except Exception:
        logger.warning(
            "Failed to create QA worktree for branch %s, falling back to project path",
            execution_branch,
        )
        return project_path, None


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
    result = await get_next_qa_task(store, project_manager, spec_manager, state_machine)
    if result is None:
        logger.info("No QA tasks ready.")
        return False

    task, project, spec = result

    # Step 2: load QA config
    config = load_qa_config(project.local_path)
    if config is None:
        logger.info(
            "No QA config found for task=%s, transitioning to ready_for_deployment", task.id
        )
        await state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
        return True

    # Step 3: look up execution branch and run tool steps in that branch's worktree
    execution_events = await store.get_events(task.id, "task_executions")
    execution_branch: str | None = None
    for event in reversed(execution_events):
        if event.event_type == ev.EXECUTION_STARTED:
            bn = event.payload.get("branch_name")
            if bn:
                execution_branch = bn
            break

    qa_cwd, qa_worktree_path = _create_qa_worktree(project.local_path, execution_branch)
    try:
        step_results = run_qa_steps(config, qa_cwd)
    finally:
        if qa_worktree_path is not None:
            _remove_qa_worktree(project.local_path, qa_worktree_path)

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
        ["claude", "-p", "--allowedTools", "Bash,Read,Glob,Grep"],
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
) -> None:
    """React to Postgres LISTEN/NOTIFY events for task status changes and compilation triggers.

    1. Runs startup catchup by calling compile_once, run_once, and run_qa_once before listening.
    2. Enters the notification-driven loop.
    3. Queues notifications received during execution using asyncio.Queue.
    4. Processes queued items after each task completes.
    5. Compilation trigger events route to compile_once only.
    6. Task status events route to run_once + run_qa_once.

    With max_workers=1, only one task runs at a time; additional notifications
    are queued and processed sequentially after each task completes.
    """
    queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
    active = False

    async def _dispatch_one() -> None:
        """Run one pass: QA first, then implementation, then compilation."""
        did_qa = await run_qa_once(store, invoker)
        if not did_qa:
            did_impl = await run_once(store, invoker)
            if not did_impl:
                await compile_once(store)

    async def _notification_producer(listener: NotificationListener) -> None:
        async for event_tuple in listener.listen():
            await queue.put(event_tuple)

    async def _run_loop() -> None:
        nonlocal active
        # Startup catchup
        logger.info("Worker: running startup catchup")
        await _dispatch_one()
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
            else:
                task_id, status = event_tuple[1], event_tuple[2]
                logger.info(
                    "Worker: dequeued notification task=%s status=%s", task_id, status
                )
            active = True
            try:
                await _dispatch_one()
            finally:
                active = False
                queue.task_done()
                try:
                    await store.refresh_views()
                except Exception:
                    logger.warning("View refresh failed after notification", exc_info=True)

    async with NotificationListener(dsn, max_workers=max_workers) as listener:
        producer_task = asyncio.create_task(_notification_producer(listener))
        consumer_task = asyncio.create_task(_run_loop())
        try:
            done, pending = await asyncio.wait(
                [producer_task, consumer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            pending = {producer_task, consumer_task}
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


def main(watchdog_timeout: int = 300) -> None:
    """Initialize all components with PostgresStore and run once."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main_async(watchdog_timeout=watchdog_timeout))


async def _main_async(watchdog_timeout: int = 300) -> None:
    from core.db import close_pool
    from core.store import PostgresStore

    store = PostgresStore()
    invoker = ClaudeCodeInvoker(watchdog_timeout=watchdog_timeout)
    try:
        did_qa = await run_qa_once(store, invoker)
        if not did_qa:
            did_impl = await run_once(store, invoker)
            if not did_impl:
                await compile_once(store)
    finally:
        await close_pool()


async def _main_loop_async(watchdog_timeout: int = 300) -> None:
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
        await notification_loop(store, invoker, dsn)
    except asyncio.CancelledError:
        logger.info("Worker stopped.")
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        await close_pool()


def main_loop_entry(watchdog_timeout: int = 300) -> None:
    """Initialize all components with PostgresStore and run the continuous loop."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main_loop_async(watchdog_timeout=watchdog_timeout))
