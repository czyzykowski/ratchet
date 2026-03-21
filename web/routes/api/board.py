"""API: GET /api/board — returns tasks grouped by status as JSON."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import events as ev
from web.board_builder import STATUS_LABELS, STATUS_ORDER, get_task_status, load_board

router = APIRouter()


@router.get("/activity")
async def get_activity(request: Request) -> JSONResponse:
    store = request.app.state.store
    all_tasks, project_by_id, task_events_cache = await load_board(store)

    task_by_id = {task["id"]: task for task in all_tasks}

    activity = []
    for task_id, task_events in task_events_cache.items():
        task = task_by_id.get(task_id)
        if task is None:
            continue
        project = project_by_id.get(task["project_id"])
        project_name = project.name if project is not None else str(task["project_id"])

        prev_status: str | None = None
        for event in task_events:
            if event.event_type == ev.TASK_CREATED:
                prev_status = event.payload.get("status", ev.READY_FOR_SPEC)
            elif event.event_type == ev.TASK_STATUS_CHANGED:
                to_status = event.payload["to_status"]
                activity.append({
                    "task_id": str(task_id),
                    "task_title": task["title"],
                    "project_name": project_name,
                    "from_status": prev_status,
                    "to_status": to_status,
                    "occurred_at": event.occurred_at.isoformat(),
                })
                prev_status = to_status

    activity.sort(key=lambda x: x["occurred_at"], reverse=True)
    return JSONResponse({"events": activity[:40]})


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

        # Surface baseline QA failure for ready_for_implementation tasks
        baseline_qa_failure: str | None = None
        if status == ev.READY_FOR_IMPLEMENTATION:
            task_events = task_events_cache.get(task["id"], [])
            last_failed_seq: int | None = None
            last_cleared_seq: int | None = None
            failure_output: str | None = None
            for e in task_events:
                if e.event_type == ev.TASK_BASELINE_QA_FAILED:
                    last_failed_seq = e.sequence
                    failure_output = e.payload.get("failure_output")
                elif e.event_type in (ev.TASK_BASELINE_QA_RETRY, ev.TASK_FORCE_EXECUTE):
                    last_cleared_seq = max(last_cleared_seq or 0, e.sequence)
            if (
                last_failed_seq is not None
                and (last_cleared_seq is None or last_failed_seq > last_cleared_seq)
            ):
                baseline_qa_failure = failure_output

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
                "baseline_qa_failure": baseline_qa_failure,
                "required_capabilities": task.get("required_capabilities", []),
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
