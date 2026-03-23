#!/usr/bin/env python3
"""Show detailed status of a task including recent executions and failure reasons.

Usage: python scripts/task-status.py --task-id <uuid>
       python scripts/task-status.py --all-active
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import UUID


def _require_db_url() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)


async def show_task(task_id: UUID) -> None:
    from core import events as ev
    from core.execution_manager import ExecutionManager
    from core.project_manager import ProjectManager
    from core.state_machine import TaskStateMachine
    from core.store import PostgresStore
    from core.task_manager import TaskManager

    store = PostgresStore()
    tm = TaskManager(store)
    pm = ProjectManager(store)
    sm = TaskStateMachine(store)
    em = ExecutionManager(store, "")

    task = await tm.get_task(task_id)
    if task is None:
        print(f"Task {task_id} not found")
        return

    project = await pm.get_project(task.project_id)
    project_name = project.name if project else "?"
    status = await sm.get_current_status(task_id)
    now = datetime.now(UTC)
    age = (now - task.updated_at).total_seconds()

    print(f"Task:    {task.title}")
    print(f"Project: {project_name}")
    print(f"Status:  {status}")
    print(f"Updated: {task.updated_at.isoformat()} ({age / 60:.0f}m ago)")
    print(f"Caps:    {task.required_capabilities}")
    print(f"Depends: {task.depends_on}")
    print()

    # Executions
    execs = await em.get_execution_history(task_id)
    impl_ok = sum(1 for e in execs if "execution/" in (e.branch_name or "") and e.status == "completed")
    impl_fail = sum(1 for e in execs if "execution/" in (e.branch_name or "") and e.status == "failed")
    qa_ok = sum(1 for e in execs if "qa/" in (e.branch_name or "") and e.status == "completed")
    qa_fail = sum(1 for e in execs if "qa/" in (e.branch_name or "") and e.status == "failed")

    print(f"Executions: {len(execs)} total")
    print(f"  Impl: {impl_ok} ok, {impl_fail} failed")
    print(f"  QA:   {qa_ok} ok, {qa_fail} failed")
    print()

    # Last 5 executions
    print("Recent executions:")
    for e in execs[-5:]:
        trace = await store.get_trace(e.id)
        trace_size = len(trace.content) if trace else 0
        print(f"  {e.id} {e.status:10s} {e.branch_name or '?':35s} trace={trace_size}")
        if e.failure_reason:
            print(f"    failure: {e.failure_reason[:150]}")
    print()

    # Failure reasons from blocked transitions
    task_events = await store.get_events(task_id, "task")
    blocked = [
        e for e in task_events
        if e.event_type == ev.TASK_STATUS_CHANGED and e.payload.get("to_status") == "blocked"
    ]
    if blocked:
        print(f"Blocked {len(blocked)} times. Last reason:")
        last = blocked[-1]
        reason = last.payload.get("failure_reason", "")
        # Show first 500 chars
        print(f"  {reason[:500]}")

    # Worker assignment
    assigned = [e for e in task_events if e.event_type == ev.TASK_ASSIGNED_TO_WORKER]
    if assigned:
        last_a = assigned[-1]
        print(f"\nLast assigned to worker={last_a.payload.get('worker_id', '?')[:12]}")

    from core.db import close_pool
    await close_pool()


async def show_all_active() -> None:
    from core.project_manager import ProjectManager
    from core.store import PostgresStore
    from core.task_manager import TaskManager

    store = PostgresStore()
    tm = TaskManager(store)
    pm = ProjectManager(store)

    now = datetime.now(UTC)
    projects = await pm.list_projects()
    for project in projects:
        tasks = await tm.list_tasks_by_project(project.id)
        active = [t for t in tasks if t.status not in ("merged", "abandoned", "ready_for_spec")]
        if not active:
            continue
        print(f"=== {project.name} ===")
        for t in active:
            age = (now - t.updated_at).total_seconds() / 60
            print(f"  {t.status:25s} {age:6.0f}m  {t.title[:55]}")
        print()

    from core.db import close_pool
    await close_pool()


def main() -> None:
    _require_db_url()
    parser = argparse.ArgumentParser(description="Task status inspector")
    parser.add_argument("--task-id", type=str, help="Task UUID")
    parser.add_argument("--all-active", action="store_true", help="Show all active tasks")
    args = parser.parse_args()

    if args.all_active:
        asyncio.run(show_all_active())
    elif args.task_id:
        asyncio.run(show_task(UUID(args.task_id)))
    else:
        # Default: show all active
        asyncio.run(show_all_active())


if __name__ == "__main__":
    main()
