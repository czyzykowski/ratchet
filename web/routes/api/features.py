"""API: feature endpoints — GET /api/features, GET /api/features/{feature_id}."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.feature_manager import FeatureManager
from core.models import HighLevelSpec
from core.project_manager import ProjectManager
from core.task_manager import TaskManager

router = APIRouter()


class CreateFeatureBody(BaseModel):
    project_id: UUID
    feature_block: str  # raw text from ## FEATURE READY block
    session_id: UUID | None = None


class FinalizeFeatureBody(BaseModel):
    feature_block: str  # raw text from ## FEATURE READY block
    session_id: UUID | None = None


class CreateSimpleFeatureBody(BaseModel):
    project_id: UUID
    title: str
    description: str | None = None


def _parse_feature_block(block: str) -> tuple[str, str, list[dict[str, Any]]]:
    title_m = re.search(r"^#\s+Feature:\s+(.+)$", block, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else "Untitled Feature"

    desc_m = re.search(r"##\s+Description\s*\n(.*?)(?=##\s+High-Level Specs|$)", block, re.DOTALL)
    description = desc_m.group(1).strip() if desc_m else ""

    specs_m = re.search(r"##\s+High-Level Specs\s*\n(.*?)$", block, re.DOTALL)
    specs_block = specs_m.group(1).strip() if specs_m else ""

    specs: list[dict[str, Any]] = []
    spec_pattern = re.compile(r"###\s+(\d+)\.\s+(.+?)(?=###\s+\d+\.|$)", re.DOTALL)
    for m in spec_pattern.finditer(specs_block):
        order = int(m.group(1))
        spec_title = m.group(2).strip().split("\n")[0].strip()
        spec_body = m.group(2)

        order_m = re.search(r"\*\*Order:\*\*\s*(\d+)", spec_body)
        if order_m:
            order = int(order_m.group(1))

        deps_m = re.search(r"\*\*Dependencies:\*\*\s*(.+)", spec_body)
        raw_deps = deps_m.group(1).strip() if deps_m else "none"
        dep_indices: list[int] = []
        if raw_deps.lower() != "none":
            for d in re.split(r"[,\s]+", raw_deps):
                d = d.strip()
                if d.isdigit():
                    dep_indices.append(int(d))

        content_m = re.search(r"\*\*Content:\*\*\s*\n(.*?)(?=\*\*|\Z)", spec_body, re.DOTALL)
        content = content_m.group(1).strip() if content_m else spec_body.strip()

        specs.append({
            "title": spec_title, "order": order, "content": content, "dep_indices": dep_indices
        })

    return title, description, specs


@router.post("/features/simple", status_code=201)
async def create_simple_feature(body: CreateSimpleFeatureBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    project = await pm.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    feature = await fm.create_feature(
        body.project_id, body.title, body.description or "", session_id=None
    )

    return JSONResponse({"feature": feature.model_dump(mode="json")}, status_code=201)


@router.post("/features", status_code=201)
async def create_feature(body: CreateFeatureBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    project = await pm.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    title, description, specs = _parse_feature_block(body.feature_block)
    feature = await fm.create_feature(
        body.project_id, title, description, session_id=body.session_id
    )

    hls_by_order: dict[int, HighLevelSpec] = {}
    for spec_def in sorted(specs, key=lambda s: s["order"]):
        dep_uuids = [
            hls_by_order[idx].id
            for idx in spec_def["dep_indices"]
            if idx in hls_by_order
        ]
        hls = await fm.add_high_level_spec(
            feature_id=feature.id,
            title=spec_def["title"],
            order=spec_def["order"],
            content=spec_def["content"],
            dependencies=dep_uuids,
        )
        hls_by_order[spec_def["order"]] = hls

    return JSONResponse({"feature": feature.model_dump(mode="json")}, status_code=201)


@router.post("/features/{feature_id}/finalize", status_code=200)
async def finalize_feature(feature_id: UUID, body: FinalizeFeatureBody, request: Request) -> JSONResponse:
    """Add high-level specs (and optionally a session link) to an existing idea feature."""
    store = request.app.state.store
    fm = FeatureManager(store)

    feature = await fm.get_feature(feature_id)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Feature {feature_id} not found")

    title, description, specs = _parse_feature_block(body.feature_block)

    # Update title/description via a new event if they differ (treat as refinement)
    await fm.update_feature(feature_id, title=title, description=description, session_id=body.session_id)

    hls_by_order: dict[int, HighLevelSpec] = {}
    for spec_def in sorted(specs, key=lambda s: s["order"]):
        dep_uuids = [
            hls_by_order[idx].id
            for idx in spec_def["dep_indices"]
            if idx in hls_by_order
        ]
        hls = await fm.add_high_level_spec(
            feature_id=feature_id,
            title=spec_def["title"],
            order=spec_def["order"],
            content=spec_def["content"],
            dependencies=dep_uuids,
        )
        hls_by_order[spec_def["order"]] = hls

    updated = await fm.get_feature(feature_id)
    return JSONResponse({"feature": updated.model_dump(mode="json")})  # type: ignore[union-attr]


@router.get("/features")
async def list_features(request: Request, project_id: UUID | None = None) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    projects = await pm.list_projects()
    if project_id is not None:
        projects = [p for p in projects if p.id == project_id]
    features_list = []
    for project in projects:
        raw_features = await fm.list_features(project.id)
        for feature in raw_features:
            specs = await fm.get_high_level_specs(feature.id)
            compiled_count = sum(1 for s in specs if s.compiled)
            status = await fm.get_feature_status(feature.id)
            feature_data = feature.model_dump(mode="json")
            feature_data["compiled_spec_count"] = compiled_count
            feature_data["total_spec_count"] = len(specs)
            feature_data["project_name"] = project.name
            feature_data["status"] = status
            features_list.append(feature_data)

    return JSONResponse({"features": features_list})


@router.get("/features/{feature_id}")
async def get_feature(feature_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    fm = FeatureManager(store)

    feature = await fm.get_feature(feature_id)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Feature {feature_id} not found")

    specs = await fm.get_high_level_specs(feature_id)
    task_manager = TaskManager(store)
    specs_data = []
    for s in specs:
        task_status: str | None = None
        if s.task_id is not None:
            task = await task_manager.get_task(s.task_id)
            if task is not None:
                task_status = task.status
        specs_data.append({
            "id": str(s.id),
            "feature_id": str(s.feature_id),
            "task_id": str(s.task_id) if s.task_id else None,
            "title": s.title,
            "order": s.order,
            "content": s.content,
            "compiled": s.compiled,
            "dependencies": [str(d) for d in s.dependencies],
            "task_status": task_status,
        })

    status = await fm.get_feature_status(feature_id)
    feature_dict = feature.model_dump(mode="json")
    feature_dict["status"] = status

    return JSONResponse(
        {
            "feature": feature_dict,
            "specs": specs_data,
        }
    )
