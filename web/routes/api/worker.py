"""API: POST /api/worker/run-next — trigger worker execution."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from web.sse import broadcast_task_updated
from worker.runner import get_next_task, run_once

router = APIRouter(prefix="/worker")


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
