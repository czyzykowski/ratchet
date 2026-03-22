"""GET /api/workers — list all connected workers from WorkerRegistry."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.registry import WorkerRegistry

router = APIRouter(prefix="/workers")


@router.get("")
async def list_workers(request: Request) -> JSONResponse:
    registry: WorkerRegistry = request.app.state.registry
    return JSONResponse(
        [
            {
                "id": w.worker_id,
                "capabilities": w.capabilities,
                "current_execution_id": w.current_execution_id,
                "connected_at": w.connected_at.isoformat(),
                "status": "busy" if w.current_execution_id is not None else "idle",
            }
            for w in registry.all_workers()
        ]
    )
