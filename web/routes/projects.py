"""Projects routes: GET /projects, GET /projects/{project_id}."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import events as ev
from core.project_manager import ProjectManager
from core.store import Store
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
