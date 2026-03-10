"""Board route: GET / — task board grouped by status."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import events as ev
from core.store import Store
from web.board_builder import STATUS_LABELS, STATUS_ORDER, get_task_status, load_board
from web.templating import templates

router = APIRouter()


def _get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


@router.get("/", response_class=HTMLResponse)
async def board(request: Request) -> HTMLResponse:
    store = _get_store(request)
    all_tasks, project_by_id, task_events_cache = await load_board(store)

    tasks_by_status: dict[str, list[dict[str, Any]]] = {s: [] for s in STATUS_ORDER}
    for task in all_tasks:
        status = task["status"]
        if status in (ev.ABANDONED, ev.DEPLOYED):
            continue
        if status not in tasks_by_status:
            tasks_by_status[status] = []
        tasks_by_status[status].append(task)

    for task in all_tasks:
        unmet: list[str] = []
        for dep_id_str in task.get("depends_on", []):
            try:
                dep_id = UUID(dep_id_str)
            except ValueError:
                unmet.append(dep_id_str)
                continue
            dep_status = get_task_status(dep_id, task_events_cache)
            if dep_status != ev.DEPLOYED:
                unmet.append(dep_id_str)
        task["unmet_deps"] = unmet

    return templates.TemplateResponse(
        "board.html",
        {
            "request": request,
            "tasks_by_status": tasks_by_status,
            "project_by_id": project_by_id,
            "STATUS_ORDER": STATUS_ORDER,
            "STATUS_LABELS": STATUS_LABELS,
        },
    )
