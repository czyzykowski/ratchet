"""Specs routes: GET /specs/{spec_id}."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from web import queries
from web.templating import templates

router = APIRouter()


@router.get("/specs/{spec_id}", response_class=HTMLResponse)
async def spec_detail(spec_id: UUID, request: Request) -> Response:
    pool = request.app.state.pool  # type: ignore[attr-defined]
    async with pool.connection() as conn:
        spec = await queries.get_spec(conn, spec_id)
        if spec is None:
            return templates.TemplateResponse(
                "404.html",
                {"request": request, "message": f"Spec {spec_id} not found"},
                status_code=404,
            )
        task = await queries.get_task(conn, UUID(str(spec["task_id"])))
    return templates.TemplateResponse(
        "specs/detail.html",
        {"request": request, "spec": spec, "task": task},
    )
