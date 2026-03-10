"""Features routes: GET /features, GET /features/{feature_id}."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager
from web.templating import templates

router = APIRouter()


def _get_store(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.store


@router.get("/features", response_class=HTMLResponse)
async def features_list(request: Request) -> Response:
    store = _get_store(request)
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    projects = await pm.list_projects()
    groups = []
    for project in projects:
        features = await fm.list_features(project.id)
        feature_rows = []
        for feature in features:
            specs = await fm.get_high_level_specs(feature.id)
            compiled_count = sum(1 for s in specs if s.compiled)
            feature_rows.append(
                {
                    "feature": feature,
                    "compiled_count": compiled_count,
                    "total_count": len(specs),
                }
            )
        groups.append({"project": project, "features": feature_rows})

    return templates.TemplateResponse(
        "features.html",
        {"request": request, "groups": groups},
    )


@router.get("/features/{feature_id}", response_class=HTMLResponse)
async def feature_detail(feature_id: UUID, request: Request) -> Response:
    store = _get_store(request)
    fm = FeatureManager(store)
    pm = ProjectManager(store)

    feature = await fm.get_feature(feature_id)
    if feature is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Feature {feature_id} not found"},
            status_code=404,
        )

    specs = await fm.get_high_level_specs(feature_id)
    hls_by_id = {s.id: s for s in specs}
    project = await pm.get_project(feature.project_id)

    return templates.TemplateResponse(
        "feature_detail.html",
        {
            "request": request,
            "feature": feature,
            "project": project,
            "specs": specs,
            "hls_by_id": hls_by_id,
        },
    )
