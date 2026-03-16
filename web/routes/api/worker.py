"""API: Worker control endpoints."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from web.sse import broadcast_task_updated
from worker.runner import get_next_task, run_once
from worker.service import WorkerService, WorkerSettings

router = APIRouter(prefix="/worker")


class StopBody(BaseModel):
    graceful: bool = True


class SettingsBody(BaseModel):
    watchdog_timeout: Optional[int] = None
    max_workers: Optional[int] = None
    local_capabilities: Optional[list[str]] = None
    enabled: Optional[bool] = None


def _status_response(ws: WorkerService) -> dict[str, object]:
    started_at = ws.started_at
    uptime_seconds = None
    if started_at is not None:
        uptime_seconds = (datetime.now(UTC) - started_at).total_seconds()
    return {
        "status": ws.status,
        "started_at": started_at.isoformat() if started_at is not None else None,
        "error_message": ws.error_message,
        "settings": dataclasses.asdict(ws.settings),
        "uptime_seconds": uptime_seconds,
    }


@router.post("/run-next")
async def run_next(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    store = request.app.state.store
    pm = ProjectManager(store)
    sm = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, pm, sm, state_machine)
    if result is None:
        return JSONResponse({"status": "no_tasks_ready"})

    task, _project, _spec = result
    background_tasks.add_task(run_once, store)
    broadcast_task_updated(request.app)
    return JSONResponse({"status": "started", "task_id": str(task.id), "title": task.title})


@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    ws: WorkerService = request.app.state.worker_service
    return JSONResponse(_status_response(ws))


@router.post("/start")
async def start_worker(request: Request) -> JSONResponse:
    ws: WorkerService = request.app.state.worker_service
    try:
        await ws.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(_status_response(ws))


@router.post("/stop")
async def stop_worker(request: Request, body: StopBody = StopBody()) -> JSONResponse:
    ws: WorkerService = request.app.state.worker_service
    if ws.status == "stopped":
        raise HTTPException(status_code=409, detail="Worker is already stopped")
    await ws.stop(graceful=body.graceful)
    return JSONResponse(_status_response(ws))


@router.post("/restart")
async def restart_worker(request: Request, body: StopBody = StopBody()) -> JSONResponse:
    ws: WorkerService = request.app.state.worker_service
    await ws.restart(graceful=body.graceful)
    return JSONResponse(_status_response(ws))


@router.patch("/settings")
async def update_settings(request: Request, body: SettingsBody) -> JSONResponse:
    ws: WorkerService = request.app.state.worker_service
    current = ws.settings
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    merged = dataclasses.replace(current, **updates)
    await ws.update_settings(merged)
    return JSONResponse(dataclasses.asdict(ws.settings))
