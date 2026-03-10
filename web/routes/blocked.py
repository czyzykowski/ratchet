"""Blocked tasks route: GET /blocked."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import events as ev
from core.store import Store
from web.board_builder import load_board
from web.templating import templates

router = APIRouter()


def _get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


async def _find_last_failure_reason(task_id: UUID) -> str | None:
    """Find the failure reason for the most recent blocking event for this task.

    Two-stage lookup:
    1. Checks task.status_changed events with to_status='blocked' and failure_reason.
    2. Falls back to execution.failed events referencing this task_id.
    """
    from core.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT payload->>'failure_reason'
                FROM events
                WHERE aggregate_type = 'task'
                  AND aggregate_id = %s
                  AND event_type = 'task.status_changed'
                  AND payload->>'to_status' = 'blocked'
                  AND payload->>'failure_reason' IS NOT NULL
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (str(task_id),),
            )
            row = await cur.fetchone()
    if row:
        return str(row[0])

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT payload->>'failure_reason'
                FROM events
                WHERE aggregate_type = 'execution'
                  AND event_type = 'execution.failed'
                  AND aggregate_id IN (
                    SELECT aggregate_id FROM events
                    WHERE aggregate_type = 'execution'
                      AND event_type = 'execution.started'
                      AND payload->>'task_id' = %s
                  )
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (str(task_id),),
            )
            row = await cur.fetchone()
    if row:
        return str(row[0])
    return None


@router.get("/blocked", response_class=HTMLResponse)
async def blocked(request: Request) -> HTMLResponse:
    from core.spec_manager import SpecManager

    store = _get_store(request)
    all_tasks, project_by_id, _ = await load_board(store)
    spec_manager = SpecManager(store)

    blocked_tasks: list[dict[str, Any]] = []
    for task in all_tasks:
        if task["status"] != ev.BLOCKED:
            continue

        task_id: UUID = task["id"]
        project = project_by_id.get(task["project_id"])
        project_name = project.name if project else "unknown"

        current_spec = await spec_manager.get_current_spec(task_id)
        spec_id = str(current_spec.id) if current_spec is not None else None

        failure_reason = await _find_last_failure_reason(task_id)

        blocked_tasks.append({
            "id": task_id,
            "title": task["title"],
            "project_name": project_name,
            "spec_id": spec_id,
            "refinement_count": task.get("refinement_count", 0),
            "failure_reason": failure_reason,
        })

    return templates.TemplateResponse(
        "blocked.html",
        {
            "request": request,
            "blocked_tasks": blocked_tasks,
            "count": len(blocked_tasks),
        },
    )
