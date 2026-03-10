"""Tasks route: GET /tasks/{task_id}."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import events as ev
from core.models import Event
from core.project_manager import ProjectManager
from core.store import Store
from web.board_builder import build_task, get_task_status
from web.templating import templates

router = APIRouter()


def _get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: UUID, request: Request) -> HTMLResponse:
    store = _get_store(request)
    task_events = await store.get_events(task_id, "task")
    if not task_events:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Task {task_id} not found"},
            status_code=404,
        )

    project_id: UUID | None = None
    for event in task_events:
        if event.event_type == ev.TASK_CREATED:
            pid_str = event.payload.get("project_id")
            if pid_str:
                project_id = UUID(pid_str)
            break

    if project_id is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Task {task_id} has no project"},
            status_code=404,
        )

    task = build_task(task_id, project_id, task_events)
    if task is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Task {task_id} not found"},
            status_code=404,
        )

    pm = ProjectManager(store)
    project = await pm.get_project(project_id)

    depends_on = task.get("depends_on", [])
    task_events_cache: dict[UUID, list[Event]] = {task_id: task_events}
    dep_statuses: dict[str, str | None] = {}
    for dep_id_str in depends_on:
        try:
            dep_id = UUID(dep_id_str)
        except ValueError:
            dep_statuses[dep_id_str] = None
            continue
        dep_events = await store.get_events(dep_id, "task")
        task_events_cache[dep_id] = dep_events
        dep_statuses[dep_id_str] = get_task_status(dep_id, task_events_cache)

    return templates.TemplateResponse(
        "task_detail.html",
        {
            "request": request,
            "task": task,
            "project": project,
            "dep_statuses": dep_statuses,
        },
    )
