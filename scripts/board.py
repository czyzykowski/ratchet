"""Print the current task board grouped by status across all projects."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from core import events as ev

STATUS_ORDER = [
    ev.READY_FOR_SPEC,
    ev.SPEC_QA,
    ev.READY_FOR_IMPLEMENTATION,
    ev.IN_PROGRESS,
    ev.BLOCKED,
    ev.READY_FOR_QA,
    ev.READY_FOR_DEPLOYMENT,
    ev.DEPLOYED,
]

STATUS_LABELS = {
    ev.READY_FOR_SPEC: "READY FOR SPEC",
    ev.SPEC_QA: "SPEC QA",
    ev.READY_FOR_IMPLEMENTATION: "READY FOR IMPLEMENTATION",
    ev.IN_PROGRESS: "IN PROGRESS",
    ev.BLOCKED: "BLOCKED",
    ev.READY_FOR_QA: "READY FOR QA",
    ev.READY_FOR_DEPLOYMENT: "READY FOR DEPLOYMENT",
    ev.DEPLOYED: "DEPLOYED",
}


def _build_task(task_id: UUID, project_id: UUID, task_events: list) -> dict | None:
    task: dict | None = None
    refinement_count = 0
    for event in task_events:
        if event.event_type == ev.TASK_CREATED:
            p = event.payload
            task = {
                "id": task_id,
                "title": p.get("title", ""),
                "status": p.get("status", ev.READY_FOR_SPEC),
                "project_id": project_id,
                "refinement_count": 0,
            }
        elif event.event_type == ev.TASK_STATUS_CHANGED and task is not None:
            task["status"] = event.payload["to_status"]
        elif event.event_type == ev.TASK_SPEC_ASSIGNED:
            refinement_count += 1
    if task is not None:
        task["refinement_count"] = refinement_count
    return task


async def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current task board.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show full 36-char task UUIDs instead of truncated 8-char IDs.")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        projects = await pm.list_projects()
        project_by_id = {p.id: p for p in projects}

        tasks_by_status: dict[str, list[dict]] = {s: [] for s in STATUS_ORDER}

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
                task_events = await store.get_events(task_id, "task")
                task = _build_task(task_id, project.id, task_events)
                if task is not None:
                    status = task["status"]
                    if status not in tasks_by_status:
                        tasks_by_status[status] = []
                    tasks_by_status[status].append(task)

        print("=== RATCHET BOARD ===")

        total = sum(len(v) for v in tasks_by_status.values())
        if total == 0:
            print("\nNo tasks found.")
            return

        for status in STATUS_ORDER:
            task_list = tasks_by_status.get(status, [])
            if not task_list:
                continue
            label = STATUS_LABELS.get(status, status.upper())
            print(f"\n{label} ({len(task_list)})")
            for task in task_list:
                task_id_display = str(task["id"]) if args.verbose else str(task["id"])[:8]
                project_name = project_by_id.get(task["project_id"], None)
                project_label = project_name.name if project_name else "unknown"
                count = task["refinement_count"]
                ref_label = f"{count} refinement{'s' if count != 1 else ''}"
                print(f"  [{task_id_display}] {task['title']} — {project_label} — {ref_label}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
