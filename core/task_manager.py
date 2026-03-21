"""Task manager: creates tasks and replays task events."""

from __future__ import annotations

from uuid import UUID, uuid4

from core import events as ev
from core.models import Event, Task
from core.store import Store


class TaskManager:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def create_task(
        self,
        project_id: UUID,
        title: str,
        depends_on: list[str] | None = None,
        required_capabilities: list[str] | None = None,
        project_capabilities: list[str] | None = None,
    ) -> Task:
        """Create a new task in ready_for_spec status.

        Appends TASK_CREATED event under the task aggregate and a registry event
        under project_tasks aggregate. Returns the created Task.
        """
        if depends_on is None:
            depends_on = []
        if required_capabilities is None:
            required_capabilities = []
        if project_capabilities is None:
            project_capabilities = []
        # Union task-specific and project-level capabilities (deduplicated)
        merged_capabilities = list(dict.fromkeys(project_capabilities + required_capabilities))
        task_id = uuid4()
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_CREATED,
            payload={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "title": title,
                "status": ev.READY_FOR_SPEC,
                "required_capabilities": merged_capabilities,
            },
        )
        await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project_tasks",
            event_type=ev.TASK_CREATED,
            payload={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "title": title,
            },
        )
        if depends_on:
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPENDENCY_ADDED,
                payload={"depends_on": depends_on},
            )
        task_events = await self._store.get_events(task_id, "task")
        result = await self._replay_task(task_id, task_events)
        assert result is not None
        return result

    async def get_task(self, task_id: UUID) -> Task | None:
        """Return a Task by replaying its events, or None if not found."""
        task_events = await self._store.get_events(task_id, "task")
        return await self._replay_task(task_id, task_events)

    async def update_task_capabilities(
        self, task_id: UUID, required_capabilities: list[str]
    ) -> Task:
        """Overwrite required_capabilities on a task by appending TASK_CAPABILITIES_UPDATED event.

        Raises ValueError if task not found.
        Returns updated Task.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_CAPABILITIES_UPDATED,
            payload={"required_capabilities": required_capabilities},
        )
        updated = await self.get_task(task_id)
        assert updated is not None
        return updated

    async def list_tasks_by_project(self, project_id: UUID) -> list[Task]:
        """Return all tasks for a project using the project_tasks registry."""
        project_task_events = await self._store.get_events(project_id, "project_tasks")
        task_ids_seen: set[UUID] = set()
        task_ids: list[UUID] = []
        for event in project_task_events:
            tid_str = event.payload.get("task_id")
            if tid_str:
                tid = UUID(tid_str)
                if tid not in task_ids_seen:
                    task_ids_seen.add(tid)
                    task_ids.append(tid)
        tasks: list[Task] = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task is not None:
                tasks.append(task)
        return tasks

    async def _replay_task(self, task_id: UUID, events: list[Event]) -> Task | None:
        """Replay task events to build a Task model. Returns None if no TASK_CREATED event found."""
        task: Task | None = None
        depends_on: list[str] = []

        for event in events:
            if event.event_type == ev.TASK_CREATED:
                p = event.payload
                task = Task(
                    id=task_id,
                    project_id=UUID(p["project_id"]),
                    title=p.get("title", ""),
                    status=p.get("status", ev.READY_FOR_SPEC),
                    current_spec_id=None,
                    refinement_count=0,
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                    required_capabilities=p.get("required_capabilities", []),
                )
            elif event.event_type == ev.TASK_STATUS_CHANGED and task is not None:
                update: dict[str, object] = {
                    "status": event.payload["to_status"],
                    "updated_at": event.occurred_at,
                }
                if event.payload.get("to_status") == ev.DEPLOYED:
                    update["merge_commit_sha"] = event.payload.get("merge_commit_sha")
                task = task.model_copy(update=update)
            elif event.event_type == ev.TASK_SPEC_ASSIGNED and task is not None:
                spec_id_str = event.payload.get("spec_id")
                current_spec_id = UUID(spec_id_str) if spec_id_str else None
                task = task.model_copy(
                    update={
                        "current_spec_id": current_spec_id,
                        "refinement_count": task.refinement_count + 1,
                        "updated_at": event.occurred_at,
                    }
                )
            elif event.event_type == ev.TASK_DEPENDENCY_ADDED:
                depends_on.extend(event.payload.get("depends_on", []))
            elif (
                event.event_type in (ev.TASK_TITLE_CHANGED, ev.TASK_TITLE_UPDATED)
                and task is not None
            ):
                task = task.model_copy(
                    update={
                        "title": event.payload["title"],
                        "updated_at": event.occurred_at,
                    }
                )
            elif event.event_type == ev.TASK_CAPABILITIES_UPDATED and task is not None:
                task = task.model_copy(
                    update={
                        "required_capabilities": event.payload.get("required_capabilities", []),
                        "updated_at": event.occurred_at,
                    }
                )

        if task is not None:
            task = task.model_copy(update={"depends_on": depends_on})

        return task
