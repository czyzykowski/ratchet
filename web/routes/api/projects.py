"""API: project endpoints — GET /api/projects, GET /api/projects/{id}, POST /api/projects."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.project_manager import OnboardingError, ProjectManager
from web.board_builder import load_board

router = APIRouter()


class CreateProjectBody(BaseModel):
    name: str
    path: str


@router.get("/projects")
async def list_projects(request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    projects = await pm.list_projects()
    return JSONResponse({"projects": [p.model_dump(mode="json") for p in projects]})


@router.get("/projects/{project_id}")
async def get_project(project_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    project = await pm.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    all_tasks, _, _ = await load_board(store)
    project_tasks: list[dict[str, Any]] = [
        {
            "id": str(t["id"]),
            "title": t["title"],
            "status": t["status"],
            "project_id": str(t["project_id"]),
            "depends_on": t.get("depends_on", []),
        }
        for t in all_tasks
        if t["project_id"] == project_id
    ]

    return JSONResponse(
        {
            "project": project.model_dump(mode="json"),
            "tasks": project_tasks,
        }
    )


@router.post("/projects", status_code=201)
async def create_project(body: CreateProjectBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    try:
        project = await pm.register_project(
            name=body.name, repo_url=body.path, local_path=body.path
        )
    except OnboardingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"project": project.model_dump(mode="json")}, status_code=201)
