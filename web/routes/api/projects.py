"""API: project endpoints — list, get, create, and update projects."""

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
    repo_url: str | None = None
    config_source: str = "disk"
    claude_md: str | None = None
    intent_md: str | None = None
    ratchet_yaml: str | None = None


class UpdateProjectBody(BaseModel):
    name: str
    repo_url: str
    local_path: str
    config_source: str = "disk"
    claude_md: str | None = None
    intent_md: str | None = None
    ratchet_yaml: str | None = None


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
            "project_id": str(t["project_id"]),
            "title": t["title"],
            "status": t["status"],
            "refinement_count": t.get("refinement_count", 0),
            "created_at": t["updated_at"].isoformat() if t.get("updated_at") is not None else None,
            "updated_at": t["updated_at"].isoformat() if t.get("updated_at") is not None else None,
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
            name=body.name,
            repo_url=body.repo_url or body.path,
            local_path=body.path,
            config_source=body.config_source,
        )
    except OnboardingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if body.config_source == "db" and any(
        f is not None for f in (body.claude_md, body.intent_md, body.ratchet_yaml)
    ):
        await pm.update_project_config(
            project.id, body.claude_md, body.intent_md, body.ratchet_yaml
        )
        refreshed = await pm.get_project(project.id)
        if refreshed is not None:
            project = refreshed
    return JSONResponse({"project": project.model_dump(mode="json")}, status_code=201)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: UUID, body: UpdateProjectBody, request: Request
) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    existing = await pm.get_project(project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    await pm.update_project(
        project_id,
        name=body.name,
        repo_url=body.repo_url,
        local_path=body.local_path,
        config_source=body.config_source,
    )
    if body.config_source == "db":
        await pm.update_project_config(
            project_id, body.claude_md, body.intent_md, body.ratchet_yaml
        )
    updated = await pm.get_project(project_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return JSONResponse({"project": updated.model_dump(mode="json")})
