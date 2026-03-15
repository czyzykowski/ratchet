"""API: GET /api/executions/{execution_id} — returns execution detail with trace."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core import events as ev

router = APIRouter()


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    exec_events = await store.get_events(execution_id, "execution")

    if not exec_events:
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")

    execution: dict[str, Any] = {}
    for event in exec_events:
        if event.event_type == ev.EXECUTION_STARTED:
            p = event.payload
            execution = {
                "id": str(execution_id),
                "task_id": p["task_id"],
                "spec_id": p["spec_id"],
                "branch_name": p.get("branch_name"),
                "started_at": p.get("started_at") or event.occurred_at.isoformat(),
                "status": "in_progress",
                "completed_at": None,
                "failure_reason": None,
            }
        elif event.event_type == ev.EXECUTION_COMPLETED:
            execution["status"] = "completed"
            execution["completed_at"] = event.occurred_at.isoformat()
        elif event.event_type == ev.EXECUTION_FAILED:
            execution["status"] = "failed"
            execution["failure_reason"] = event.payload.get("failure_reason")
            execution["completed_at"] = event.occurred_at.isoformat()

    trace = await store.get_trace(execution_id)
    trace_content: str | None = trace.content if trace is not None else None

    return JSONResponse({"execution": execution, "trace": trace_content})
