"""TaskFinder: shared task discovery with filtering and dependency checking."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core.managers import Managers
from core.models import Project, Task
from core.store import Store


class TaskFinder:
    def __init__(self, managers: Managers) -> None:
        self._store: Store = managers.store
        self._project_manager = managers.projects
        self._task_manager = managers.tasks

    async def find(
        self,
        statuses: set[str],
        project_id: UUID | None = None,
        *,
        predicate: Any | None = None,
        skip_project_if_status: str | None = None,
    ) -> list[tuple[Task, Project, list[Any]]]:
        active_projects = await self._project_manager.list_projects()
        if project_id is not None:
            active_projects = [p for p in active_projects if p.id == project_id]

        results: list[tuple[Task, Project, list[Any]]] = []

        for project in active_projects:
            project_task_events = await self._store.get_events(project.id, "project_tasks")

            task_ids_seen: set[UUID] = set()
            task_ids_ordered: list[UUID] = []
            for event in project_task_events:
                tid_str = event.payload.get("task_id")
                if tid_str:
                    tid = UUID(tid_str)
                    if tid not in task_ids_seen:
                        task_ids_seen.add(tid)
                        task_ids_ordered.append(tid)

            # Check for skip_project_if_status
            if skip_project_if_status is not None:
                skip = False
                all_tasks: list[tuple[Task | None, UUID]] = []
                for task_id in task_ids_ordered:
                    task = await self._task_manager.get_task(task_id)
                    all_tasks.append((task, task_id))
                    if task is not None and task.status == skip_project_if_status:
                        skip = True
                        break
                if skip:
                    continue
                # Use pre-loaded tasks
                for task, task_id in all_tasks:
                    if task is None or task.status not in statuses:
                        continue
                    task_events: list[Any] = []
                    if predicate is not None:
                        task_events = await self._store.get_events(task_id, "task")
                        if not predicate(task, task_events):
                            continue
                    results.append((task, project, task_events))
            else:
                for task_id in task_ids_ordered:
                    task = await self._task_manager.get_task(task_id)
                    if task is None or task.status not in statuses:
                        continue
                    task_events = []
                    if predicate is not None:
                        task_events = await self._store.get_events(task_id, "task")
                        if not predicate(task, task_events):
                            continue
                    results.append((task, project, task_events))

        results.sort(key=lambda r: r[0].created_at)
        return results
