"""Worker routes: POST /worker/run-next."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import RedirectResponse, Response

from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import PostgresStore
from worker.runner import get_next_task, run_once

router = APIRouter(prefix="/worker")


@router.post("/run-next")
async def run_next(request: Request, background_tasks: BackgroundTasks) -> Response:
    pool = request.app.state.pool
    store = PostgresStore(pool)
    pm = ProjectManager(store)
    sm = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, pm, sm, state_machine)
    if result is None:
        request.session["flash"] = "No tasks ready"
    else:
        task, _project, _spec = result
        request.session["flash"] = f"Started: {task.title} (task/{task.id})"
        background_tasks.add_task(run_once, store)

    return RedirectResponse(url="/", status_code=303)
