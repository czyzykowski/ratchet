"""API: Worker control endpoints."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from web.local_worker import LocalWorkerManager
from web.sse import broadcast_task_updated
from worker.runner import get_next_task, run_once

router = APIRouter(prefix="/worker")


class StopBody(BaseModel):
    graceful: bool = True


class SettingsBody(BaseModel):
    enabled: bool | None = None
    capabilities: list[str] | None = None
    port: int | None = None


def _status_response(lw: LocalWorkerManager) -> dict[str, object]:
    started_at = lw.started_at
    uptime_seconds = None
    if started_at is not None:
        uptime_seconds = (datetime.now(UTC) - started_at).total_seconds()
    return {
        "status": lw.status,
        "started_at": started_at.isoformat() if started_at is not None else None,
        "error_message": lw.error_message,
        "pid": lw.pid,
        "settings": dataclasses.asdict(lw.settings),
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
    lw: LocalWorkerManager = request.app.state.worker_service
    return JSONResponse(_status_response(lw))


@router.post("/start")
async def start_worker(request: Request) -> JSONResponse:
    lw: LocalWorkerManager = request.app.state.worker_service
    try:
        await lw.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(_status_response(lw))


@router.post("/stop")
async def stop_worker(request: Request, body: StopBody = StopBody()) -> JSONResponse:
    lw: LocalWorkerManager = request.app.state.worker_service
    if lw.status == "stopped":
        raise HTTPException(status_code=409, detail="Worker is already stopped")
    await lw.stop(graceful=body.graceful)
    return JSONResponse(_status_response(lw))


@router.post("/restart")
async def restart_worker(request: Request, body: StopBody = StopBody()) -> JSONResponse:
    lw: LocalWorkerManager = request.app.state.worker_service
    await lw.restart(graceful=body.graceful)
    return JSONResponse(_status_response(lw))


@router.patch("/settings")
async def update_settings(request: Request, body: SettingsBody) -> JSONResponse:
    lw: LocalWorkerManager = request.app.state.worker_service
    current = lw.settings
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    merged = dataclasses.replace(current, **updates)
    await lw.update_settings(merged)
    return JSONResponse(dataclasses.asdict(lw.settings))
