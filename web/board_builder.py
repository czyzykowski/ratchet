"""Board builder: replay helpers extracted from scripts/board.py."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core import events as ev
from core.models import Event
from core.store import Store
from core.task_manager import TaskManager

STATUS_ORDER = [
    ev.READY_FOR_SPEC,
    ev.SPEC_QA,
    ev.READY_FOR_IMPLEMENTATION,
    ev.IN_PROGRESS,
    ev.BLOCKED,
    ev.READY_FOR_QA,
    ev.READY_FOR_DEPLOYMENT,
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
    ev.ABANDONED: "ABANDONED",
}


def get_task_status(task_id: UUID, task_events_cache: dict[UUID, list[Event]]) -> str | None:
    """Return the current status of a task from the event cache, or None if unknown."""
    events = task_events_cache.get(task_id)
    if not events:
        return None
    status = None
    for event in events:
        if event.event_type == ev.TASK_CREATED:
            status = event.payload.get("status", ev.READY_FOR_SPEC)
        elif event.event_type == ev.TASK_STATUS_CHANGED:
            status = event.payload["to_status"]
    return status


async def load_board(
    store: Store,
) -> tuple[list[dict[str, Any]], dict[UUID, Any], dict[UUID, list[Event]]]:
    """Load all projects and tasks from the store.

    Returns:
        (all_tasks, project_by_id, task_events_cache)
    """
    from core.project_manager import ProjectManager

    pm = ProjectManager(store)
    task_manager = TaskManager(store)
    projects = await pm.list_projects()
    project_by_id: dict[UUID, Any] = {p.id: p for p in projects}

    all_tasks: list[dict[str, Any]] = []
    task_events_cache: dict[UUID, list[Event]] = {}

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
            task_events_cache[task_id] = task_events
            task = await task_manager.get_task(task_id)
            if task is not None:
                all_tasks.append({
                    **task.model_dump(),
                    "has_spec": task.refinement_count > 0,
                })

    return all_tasks, project_by_id, task_events_cache
