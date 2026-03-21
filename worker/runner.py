"""Worker runner: notification loop and CLI entry points.

Dispatch logic lives in worker.dispatcher.ProjectDispatcher.
This module owns asyncio lifecycle, LISTEN/NOTIFY, and signal handling.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from core import events as ev
from core.invoker import ClaudeCodeInvoker
from core.store import Store
from worker.dispatcher import ProjectDispatcher  # noqa: F401 — re-exported for backwards compat
from worker.listener import NotificationListener
from worker.worktree import QAWorktreeError  # noqa: F401
from worker.worktree import create_baseline_worktree as _create_baseline_worktree  # noqa: F401
from worker.worktree import create_qa_worktree as _create_qa_worktree  # noqa: F401
from worker.worktree import remove_qa_worktree as _remove_qa_worktree  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backwards-compatible top-level functions
# These delegate to ProjectDispatcher methods. Existing callers (scripts,
# web routes, tests) can continue to import from worker.runner.
# ---------------------------------------------------------------------------


async def recover_orphaned_tasks(store: Store) -> int:
    """Backwards-compatible wrapper around ProjectDispatcher.recover_orphans."""
    from core.invoker import ClaudeCodeInvoker as _Invoker
    invoker = _Invoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker)
    return await dispatcher.recover_orphans()


async def get_next_task(
    store: Store,
    project_manager: object,
    spec_manager: object,
    state_machine: object,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> tuple[Any, ...] | None:
    """Backwards-compatible wrapper. Returns (task, project, spec) or None."""
    from core.invoker import ClaudeCodeInvoker as _Invoker
    invoker = _Invoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    from worker.capability_check import capabilities_met
    candidates = await dispatcher._find_tasks(
        statuses={"ready_for_implementation", "waiting_for_input"},
        project_id=project_id,
        skip_project_if_status="in_progress",
    )
    from core import qa_manager
    from core.spec_manager import SpecManager
    sm = SpecManager(store)
    from core.task_manager import TaskManager
    tm = TaskManager(store)
    for t, p, _task_events in candidates:
        if not capabilities_met(t, p, local_capabilities):
            continue
        if t.status == "waiting_for_input":
            pending = await qa_manager.get_pending_question(store, t.id)
            if pending is not None:
                continue
        if t.depends_on:
            unmet = False
            for dep_id_str in t.depends_on:
                try:
                    dep_id = UUID(dep_id_str)
                except ValueError:
                    unmet = True
                    break
                dep_task = await tm.get_task(dep_id)
                if dep_task is None or dep_task.status != ev.DEPLOYED:
                    unmet = True
                    break
            if unmet:
                continue
        spec = await sm.get_current_spec(t.id)
        if spec is None:
            continue
        return (t, p, spec)
    return None


async def run_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> bool:
    """Backwards-compatible wrapper around ProjectDispatcher.impl_once."""
    if invoker is None:
        invoker = ClaudeCodeInvoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    result = await dispatcher.impl_once(project_id=project_id)
    return result.action != "idle"


async def get_next_qa_task(
    store: Store,
    project_manager: object,
    spec_manager: object,
    state_machine: object,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> tuple[Any, ...] | None:
    """Backwards-compatible wrapper."""
    from core.invoker import ClaudeCodeInvoker as _Invoker
    from core.spec_manager import SpecManager
    invoker = _Invoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    from worker.capability_check import capabilities_met
    candidates = await dispatcher._find_tasks(
        statuses={"ready_for_qa"},
        project_id=project_id,
    )
    sm = SpecManager(store)
    for t, p, _ in candidates:
        if not capabilities_met(t, p, local_capabilities):
            continue
        spec = await sm.get_current_spec(t.id)
        if spec is None:
            continue
        return (t, p, spec)
    return None


async def run_qa_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> bool:
    """Backwards-compatible wrapper around ProjectDispatcher.qa_once."""
    if invoker is None:
        invoker = ClaudeCodeInvoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    result = await dispatcher.qa_once(project_id=project_id)
    return result.action != "idle"


async def merge_once(
    store: Store,
    invoker: ClaudeCodeInvoker,
    local_capabilities: list[str] = [],
    project_id: UUID | None = None,
) -> bool:
    """Backwards-compatible wrapper around ProjectDispatcher.merge_once."""
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    result = await dispatcher.merge_once(project_id=project_id)
    return result.action != "idle" and result.success


async def compile_once(store: Store) -> bool:
    """Backwards-compatible wrapper around ProjectDispatcher.compile_once."""
    from core.invoker import ClaudeCodeInvoker as _Invoker
    invoker = _Invoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker)
    result = await dispatcher.compile_once()
    return result.action != "idle"


async def poll_pr_merges(store: Store, project_local_path: str) -> None:
    """Backwards-compatible wrapper around ProjectDispatcher.poll_pr_merges."""
    from core.invoker import ClaudeCodeInvoker as _Invoker
    invoker = _Invoker(store=store)
    dispatcher = ProjectDispatcher(store, invoker)
    await dispatcher.poll_pr_merges()


async def notification_loop(
    store: Store,
    invoker: ClaudeCodeInvoker,
    dsn: str,
    max_workers: int = 1,
    local_capabilities: list[str] = [],
    _initial_busy_projects: set[UUID] | None = None,
) -> None:
    """React to Postgres LISTEN/NOTIFY events for task status changes and compilation triggers.

    1. Runs startup catchup by calling dispatch_all before listening.
    2. Enters the notification-driven loop.
    3. Queues notifications received during execution using asyncio.Queue.
    4. Processes queued items after each task completes.

    Each active project gets its own dispatch slot; busy_projects prevents concurrent
    dispatch for the same project within the same worker process.
    """
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
    active = False
    if _initial_busy_projects is not None:
        busy_projects: set[UUID] = set(_initial_busy_projects)
    else:
        busy_projects = set()

    async def _dispatch_for_project(pid: UUID) -> None:
        """Run one merge→QA→impl pass for a single project, then release busy lock."""
        try:
            await dispatcher.dispatch(pid)
        finally:
            busy_projects.discard(pid)

    async def _dispatch_all() -> None:
        """Dispatch one merge→QA→impl pass per non-busy active project, then compile once."""
        projects = await dispatcher.list_active_projects()
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
            await dispatcher.compile_once()

    async def _heartbeat_producer() -> None:
        while True:
            await asyncio.sleep(600)
            logger.info("Worker: heartbeat — queuing catchup pass")
            await queue.put(("heartbeat", "", ""))

    async def _pr_poll_loop() -> None:
        while True:
            await asyncio.sleep(300)
            try:
                await dispatcher.poll_pr_merges()
            except Exception:
                logger.warning("poll_pr_merges failed", exc_info=True)

    async def _notification_producer(listener: NotificationListener) -> None:
        async for event_tuple in listener.listen():
            await queue.put(event_tuple)

    async def _run_loop() -> None:
        nonlocal active
        # Startup orphan recovery — must run before dispatch
        await dispatcher.recover_orphans()
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
    invoker = ClaudeCodeInvoker(store=store, watchdog_timeout=watchdog_timeout)
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    try:
        result = await dispatcher.dispatch_all()
        # Log summary
        for r in result:
            if r.action != "idle":
                logger.info("dispatch: %s task=%s", r.action, r.task_id)
    finally:
        await close_pool()


async def _main_loop_async(
    watchdog_timeout: int = 300,
    local_capabilities: list[str] = [],
) -> None:
    import signal

    from core.db import close_pool
    from core.store import PostgresStore

    dsn = os.environ["DATABASE_URL"]
    store = PostgresStore()
    invoker = ClaudeCodeInvoker(store=store, watchdog_timeout=watchdog_timeout)

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
