"""Show all blocked tasks with their failure context."""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from core import events as ev


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.spec_manager import SpecManager
    from core.state_machine import TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        spec_manager = SpecManager(store)
        state_machine = TaskStateMachine(store)

        projects = await pm.list_projects()
        project_by_id = {p.id: p for p in projects}

        blocked_tasks = []

        for project in projects:
            project_task_events = await store.get_events(project.id, "project_tasks")
            task_ids_seen: set[UUID] = set()
            task_ids: list[UUID] = []
            for event in project_task_events:
                tid_str = event.payload.get("task_id")
                if tid_str:
                    tid = UUID(tid_str)
                    if tid not in task_ids_seen:
                        task_ids_seen.add(tid)
                        task_ids.append(tid)

            for task_id in task_ids:
                status = await state_machine.get_current_status(task_id)
                if status != ev.BLOCKED:
                    continue

                task_events = await store.get_events(task_id, "task")
                title = ""
                for event in task_events:
                    if event.event_type == ev.TASK_CREATED:
                        title = event.payload.get("title", "")

                current_spec = await spec_manager.get_current_spec(task_id)
                spec_id = current_spec.id if current_spec is not None else None

                # Count refinements
                refinement_count = sum(
                    1 for e in task_events if e.event_type == ev.TASK_SPEC_ASSIGNED
                )

                failure_reason = await _find_last_failure_reason(store, task_id)

                blocked_tasks.append({
                    "id": task_id,
                    "title": title,
                    "project_id": project.id,
                    "spec_id": spec_id,
                    "refinement_count": refinement_count,
                    "failure_reason": failure_reason,
                })

        print("=== BLOCKED TASKS ===")
        if not blocked_tasks:
            print("\nNo blocked tasks.")
            return

        for task in blocked_tasks:
            short_id = str(task["id"])[:8]
            project = project_by_id.get(task["project_id"])
            project_name = project.name if project else "unknown"
            print(f"\n[{short_id}] {task['title']} — {project_name}")
            if task["spec_id"]:
                print(f"Spec: {task['spec_id']}")
            print(f"Refinements: {task['refinement_count']}")
            reason = task["failure_reason"] or "(no failure reason recorded)"
            print(f"Last failure: {reason}")
    finally:
        await close_pool()


async def _find_last_failure_reason(store, task_id: UUID) -> str | None:
    """Find the failure reason from the most recent EXECUTION_FAILED event for this task.

    Execution events are stored under execution aggregate IDs. We query the events table
    directly by scanning for execution.failed events that reference this task_id in their payload.
    Since PostgresStore.get_events() requires an aggregate_id, we use a raw query via the pool.
    """
    from core.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT payload->>'failure_reason'
                FROM events
                WHERE aggregate_type = 'execution'
                  AND event_type = 'execution.failed'
                  AND aggregate_id IN (
                    SELECT aggregate_id FROM events
                    WHERE aggregate_type = 'execution'
                      AND event_type = 'execution.started'
                      AND payload->>'task_id' = %s
                  )
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (str(task_id),),
            )
            row = await cur.fetchone()
    if row:
        return row[0]
    return None


if __name__ == "__main__":
    asyncio.run(main())
