"""Action executor: executes parsed action blocks against the store."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from core import events as ev
from core.feature_manager import FeatureManager
from core.project_manager import OnboardingError, ProjectManager
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
    parsed: ParsedAction,
    store: Store,
    project_id: UUID | None,
    session_id: UUID | None = None,
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
        if parsed.action == "register_project":
            return await _register_project(parsed, store, session_id)
        elif parsed.action == "create_task":
            if project_id is None:
                return ActionResult(
                    success=False,
                    action=parsed.action,
                    message="",
                    error="create_task requires a project_id — register a project first",
                )
            return await _create_task(parsed, store, project_id)
        elif parsed.action == "create_feature":
            if project_id is None:
                return ActionResult(
                    success=False,
                    action=parsed.action,
                    message="",
                    error="create_feature requires a project_id — register a project first",
                )
            return await _create_feature(parsed, store, project_id)
        elif parsed.action == "update_task":
            return await _update_task(parsed, store)
        elif parsed.action == "archive_task":
            return await _archive_task(parsed, store)
        elif parsed.action == "add_hls":
            return await _add_hls(parsed, store)
        elif parsed.action == "check_task_status":
            return await _check_task_status(parsed, store)
        else:
            return ActionResult(
                success=False,
                action=parsed.action,
                message="",
                error=f"Unknown action: {parsed.action}",
            )
    except (InvalidTransitionError, ValueError, OnboardingError) as e:
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


async def _add_hls(parsed: ParsedAction, store: Store) -> ActionResult:
    feature_id_str = parsed.payload.get("feature_id")
    if not feature_id_str:
        raise ValueError("'feature_id' is required for add_hls")
    feature_id = UUID(str(feature_id_str))

    title = parsed.payload.get("title")
    if not title:
        raise ValueError("'title' is required for add_hls")

    order = parsed.payload.get("order")
    if order is None:
        raise ValueError("'order' is required for add_hls")

    content = parsed.payload.get("content")
    if not content:
        raise ValueError("'content' is required for add_hls")

    raw_deps = cast(list[object], parsed.payload.get("dependencies", []))
    dep_indices: list[int] = [int(cast(int, d)) for d in raw_deps]

    resolved_deps: list[UUID] = []
    if dep_indices:
        fm = FeatureManager(store)
        existing_specs = await fm.get_high_level_specs(feature_id)
        order_to_id = {spec.order: spec.id for spec in existing_specs}
        for idx in dep_indices:
            if idx not in order_to_id:
                raise ValueError(f"Dependency order {idx} not found in feature specs")
            resolved_deps.append(order_to_id[idx])

    hls = await FeatureManager(store).add_high_level_spec(
        feature_id, str(title), int(cast(int, order)), str(content), resolved_deps
    )
    return ActionResult(
        success=True,
        action="add_hls",
        message=f"✓ Added HLS: {title} (id: {hls.id})",
        entity_id=str(hls.id),
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


async def _register_project(
    parsed: ParsedAction, store: Store, session_id: UUID | None
) -> ActionResult:
    name = parsed.payload.get("name")
    if not name:
        raise ValueError("'name' is required for register_project")
    path = parsed.payload.get("path")
    if not path:
        raise ValueError("'path' is required for register_project")
    path_str = str(path)
    repo_url = str(parsed.payload.get("repo_url", path_str))
    config_source = str(parsed.payload.get("config_source", "disk"))
    raw_caps = parsed.payload.get("required_capabilities", [])
    required_capabilities: list[str] = [str(c) for c in cast(list[object], raw_caps)]

    project = await ProjectManager(store).register_project(
        str(name), repo_url, path_str, config_source, required_capabilities
    )

    if config_source == "db":
        base = pathlib.Path(path_str)
        claude_md = (base / "CLAUDE.md").read_text() if (base / "CLAUDE.md").exists() else None
        intent_md = (
            (base / "docs" / "INTENT.md").read_text()
            if (base / "docs" / "INTENT.md").exists()
            else None
        )
        ratchet_yaml = (
            (base / "ratchet.yaml").read_text() if (base / "ratchet.yaml").exists() else None
        )
        await ProjectManager(store).update_project_config(
            project.id, claude_md, intent_md, ratchet_yaml
        )

    if session_id is not None:
        await store.append_event(
            aggregate_id=session_id,
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_CONTEXT_UPDATED,
            payload={"context_id": str(project.id), "context_type": "project"},
        )

    return ActionResult(
        success=True,
        action="register_project",
        message=f"✓ Registered project: {name} (id: {project.id})",
        entity_id=str(project.id),
    )


async def _check_task_status(parsed: ParsedAction, store: Store) -> ActionResult:
    task_id_str = parsed.payload.get("task_id")
    if not task_id_str:
        raise ValueError("'task_id' is required for check_task_status")
    task_id = UUID(str(task_id_str))
    status = await TaskStateMachine(store).get_current_status(task_id)
    if status is None:
        raise ValueError(f"task not found: {task_id_str}")
    if status == ev.BLOCKED:
        task_events = await store.get_events(task_id, "task")
        reason = "unknown"
        for e in reversed(task_events):
            if e.event_type == ev.TASK_STATUS_CHANGED and e.payload.get("to_status") == ev.BLOCKED:
                reason = e.payload.get("failure_reason", "unknown")
                break
        return ActionResult(
            success=True,
            action="check_task_status",
            message=f"Task {task_id_str}: status=blocked, reason={reason}",
            entity_id=str(task_id),
        )
    return ActionResult(
        success=True,
        action="check_task_status",
        message=f"Task {task_id_str}: status={status}",
        entity_id=str(task_id),
    )
