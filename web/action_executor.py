"""Action executor: executes parsed action blocks against the store."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from core import events as ev
from core.feature_manager import FeatureManager
from core.state_machine import InvalidTransitionError, TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from web.action_parser import ParsedAction


@dataclass
class ActionResult:
    success: bool
    action: str
    message: str
    entity_id: str | None = None
    error: str | None = None


async def execute_action(
    parsed: ParsedAction, store: Store, project_id: UUID
) -> ActionResult:
    """Execute a parsed action block against the store."""
    if parsed.action == "error":
        raw_error = parsed.payload.get("error", "JSON parse error")
        return ActionResult(
            success=False,
            action="error",
            message="",
            error=str(raw_error),
        )

    try:
        if parsed.action == "create_task":
            return await _create_task(parsed, store, project_id)
        elif parsed.action == "create_feature":
            return await _create_feature(parsed, store, project_id)
        elif parsed.action == "update_task":
            return await _update_task(parsed, store)
        elif parsed.action == "archive_task":
            return await _archive_task(parsed, store)
        else:
            return ActionResult(
                success=False,
                action=parsed.action,
                message="",
                error=f"Unknown action: {parsed.action}",
            )
    except (InvalidTransitionError, ValueError) as e:
        return ActionResult(
            success=False,
            action=parsed.action,
            message="",
            error=str(e),
        )


async def _create_task(
    parsed: ParsedAction, store: Store, project_id: UUID
) -> ActionResult:
    title = parsed.payload.get("title")
    if not title:
        raise ValueError("'title' is required for create_task")
    task = await TaskManager(store).create_task(project_id, str(title))
    return ActionResult(
        success=True,
        action="create_task",
        message=f"✓ Created task: {title} (id: {task.id})",
        entity_id=str(task.id),
    )


async def _create_feature(
    parsed: ParsedAction, store: Store, project_id: UUID
) -> ActionResult:
    title = parsed.payload.get("title")
    description = parsed.payload.get("description")
    if not title:
        raise ValueError("'title' is required for create_feature")
    if description is None:
        raise ValueError("'description' is required for create_feature")
    feature = await FeatureManager(store).create_feature(
        project_id, str(title), str(description)
    )
    return ActionResult(
        success=True,
        action="create_feature",
        message=f"✓ Created feature: {title} (id: {feature.id})",
        entity_id=str(feature.id),
    )


async def _update_task(parsed: ParsedAction, store: Store) -> ActionResult:
    task_id_str = parsed.payload.get("task_id")
    if not task_id_str:
        raise ValueError("'task_id' is required for update_task")
    task_id = UUID(str(task_id_str))

    title = parsed.payload.get("title")
    status = parsed.payload.get("status")

    updates: list[str] = []

    if title is not None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_TITLE_UPDATED,
            payload={"title": str(title)},
        )
        updates.append(f"title → {title!r}")

    if status is not None:
        await TaskStateMachine(store).transition(task_id, str(status))
        updates.append(f"status → {status!r}")

    if not updates:
        raise ValueError(
            "No updates provided for update_task (need 'title' or 'status')"
        )

    return ActionResult(
        success=True,
        action="update_task",
        message=f"✓ Updated task {task_id}: {', '.join(updates)}",
        entity_id=str(task_id),
    )


async def _archive_task(parsed: ParsedAction, store: Store) -> ActionResult:
    task_id_str = parsed.payload.get("task_id")
    if not task_id_str:
        raise ValueError("'task_id' is required for archive_task")
    task_id = UUID(str(task_id_str))
    reason = parsed.payload.get("reason")
    extra_payload = {"reason": str(reason)} if reason is not None else None
    await TaskStateMachine(store).transition(
        task_id, ev.ABANDONED, extra_payload=extra_payload
    )
    return ActionResult(
        success=True,
        action="archive_task",
        message=f"✓ Archived task {task_id}",
        entity_id=str(task_id),
    )
