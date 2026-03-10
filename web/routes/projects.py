"""Projects routes: GET /projects, GET /projects/{project_id}, task creation."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from core import events as ev
from core.project_manager import ProjectManager
from core.store import Store
from web import queries
from web.board_builder import get_task_status, load_board
from web.templating import templates

router = APIRouter()


def _get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(request: Request) -> HTMLResponse:
    store = _get_store(request)
    pm = ProjectManager(store)
    project_list = await pm.list_projects()
    return templates.TemplateResponse(
        "projects.html",
        {"request": request, "projects": project_list},
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(project_id: UUID, request: Request) -> HTMLResponse:
    store = _get_store(request)
    pm = ProjectManager(store)
    project = await pm.get_project(project_id)
    if project is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Project {project_id} not found"},
            status_code=404,
        )

    all_tasks, project_by_id, task_events_cache = await load_board(store)
    project_tasks = [t for t in all_tasks if t["project_id"] == project_id]

    for task in project_tasks:
        unmet: list[str] = []
        for dep_id_str in task.get("depends_on", []):
            try:
                dep_id = UUID(dep_id_str)
            except ValueError:
                unmet.append(dep_id_str)
                continue
            dep_status = get_task_status(dep_id, task_events_cache)
            if dep_status != ev.DEPLOYED:
                unmet.append(dep_id_str)
        task["unmet_deps"] = unmet

    return templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "project": project,
            "tasks": project_tasks,
            "project_by_id": project_by_id,
        },
    )


@router.get("/projects/{project_id}/tasks/new", response_class=HTMLResponse)
async def new_task_form(project_id: UUID, request: Request) -> Response:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        project = await queries.get_project(conn, project_id)
        if project is None:
            return templates.TemplateResponse(
                "404.html",
                {"request": request, "message": f"Project {project_id} not found"},
                status_code=404,
            )
        project_tasks = await queries.get_project_tasks(conn, project_id)
    return templates.TemplateResponse(
        "tasks/new.html",
        {"request": request, "project": project, "project_tasks": project_tasks},
    )


@router.post("/projects/{project_id}/tasks")
async def create_task(
    request: Request,
    project_id: UUID,
    title: Annotated[str, Form()],
    depends_on: Annotated[list[str] | None, Form()] = None,
) -> Response:
    from core.store import PostgresStore

    pool = request.app.state.pool
    store = PostgresStore(pool)

    task_id = uuid4()

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": ev.READY_FOR_SPEC,
        },
    )

    await store.append_event(
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
        valid_deps: list[str] = []
        for dep_str in depends_on:
            try:
                UUID(dep_str)
                valid_deps.append(dep_str)
            except ValueError:
                pass
        if valid_deps:
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPENDENCY_ADDED,
                payload={"depends_on": valid_deps},
            )

    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)
