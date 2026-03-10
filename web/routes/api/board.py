"""API: GET /api/board — returns tasks grouped by status as JSON."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import events as ev
from web.board_builder import STATUS_LABELS, STATUS_ORDER, get_task_status, load_board

router = APIRouter()


@router.get("/board")
async def get_board(request: Request) -> JSONResponse:
    store = request.app.state.store
    all_tasks, project_by_id, task_events_cache = await load_board(store)

    groups_dict: dict[str, list[dict[str, Any]]] = {status: [] for status in STATUS_ORDER}

    for task in all_tasks:
        status = task["status"]
        if status not in groups_dict:
            continue

        project_id: UUID = task["project_id"]
        project = project_by_id.get(project_id)
        project_name = project.name if project is not None else str(project_id)

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

        updated_at = task.get("updated_at")
        updated_at_str = updated_at.isoformat() if updated_at is not None else None

        groups_dict[status].append(
            {
                "id": str(task["id"]),
                "title": task["title"],
                "status": task["status"],
                "project_id": str(task["project_id"]),
                "project_name": project_name,
                "updated_at": updated_at_str,
                "has_spec": task.get("has_spec", False),
                "refinement_count": task.get("refinement_count", 0),
                "unmet_deps": unmet,
            }
        )

    columns = [
        {
            "status": status,
            "label": STATUS_LABELS.get(status, status),
            "tasks": groups_dict[status],
        }
        for status in STATUS_ORDER
    ]

    return JSONResponse({"columns": columns})
