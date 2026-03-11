"""API: spec endpoints under /api/specs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.spec_manager import SpecManager

router = APIRouter()


@router.get("/specs/{spec_id}")
async def get_spec(spec_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    spec_manager = SpecManager(store)

    spec = await spec_manager.get_spec(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

    return JSONResponse(
        {
            "spec": {
                "id": str(spec.id),
                "task_id": str(spec.task_id),
                "content": spec.content,
                "created_at": spec.created_at.isoformat(),
            }
        }
    )
