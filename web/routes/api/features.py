"""API: feature endpoints — GET /api/features, GET /api/features/{feature_id}."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager

router = APIRouter()


@router.get("/features")
async def list_features(request: Request) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    projects = await pm.list_projects()
    features_list = []
    for project in projects:
        raw_features = await fm.list_features(project.id)
        for feature in raw_features:
            specs = await fm.get_high_level_specs(feature.id)
            compiled_count = sum(1 for s in specs if s.compiled)
            feature_data = feature.model_dump(mode="json")
            feature_data["compiled_spec_count"] = compiled_count
            feature_data["total_spec_count"] = len(specs)
            feature_data["project_name"] = project.name
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
    specs_data = [
        {
            "id": str(s.id),
            "feature_id": str(s.feature_id),
            "task_id": str(s.task_id) if s.task_id else None,
            "title": s.title,
            "order": s.order,
            "content": s.content,
            "compiled": s.compiled,
            "dependencies": [str(d) for d in s.dependencies],
        }
        for s in specs
    ]

    return JSONResponse(
        {
            "feature": feature.model_dump(mode="json"),
            "specs": specs_data,
        }
    )
