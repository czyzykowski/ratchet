"""Executions route: GET /executions/{execution_id}."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from core import events as ev
from core.invoker import get_traces_dir
from core.store import Store
from web.templating import templates

router = APIRouter()


def _get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


@router.get("/executions/{execution_id}", response_class=HTMLResponse)
async def execution_detail(execution_id: UUID, request: Request) -> Response:
    store = _get_store(request)
    exec_events = await store.get_events(execution_id, "execution")

    if not exec_events:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": f"Execution {execution_id} not found"},
            status_code=404,
        )

    execution: dict[str, Any] = {}
    for event in exec_events:
        if event.event_type == ev.EXECUTION_STARTED:
            p = event.payload
            execution = {
                "id": execution_id,
                "task_id": UUID(p["task_id"]),
                "spec_id": UUID(p["spec_id"]),
                "branch_name": p.get("branch_name"),
                "started_at": p.get("started_at"),
                "status": "in_progress",
                "completed_at": None,
                "failure_reason": None,
            }
        elif event.event_type == ev.EXECUTION_COMPLETED:
            execution["status"] = "completed"
            execution["completed_at"] = event.occurred_at
        elif event.event_type == ev.EXECUTION_FAILED:
            execution["status"] = "failed"
            execution["failure_reason"] = event.payload.get("failure_reason")
            execution["completed_at"] = event.occurred_at

    trace_path = Path(get_traces_dir()) / f"{execution_id}.md"
    trace_content: str | None = None
    if trace_path.exists():
        trace_content = trace_path.read_text()

    return templates.TemplateResponse(
        "execution_detail.html",
        {"request": request, "execution": execution, "trace_content": trace_content},
    )
