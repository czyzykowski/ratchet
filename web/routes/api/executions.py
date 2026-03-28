"""API: GET /api/executions/{execution_id} — returns execution detail with trace."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core import events as ev
from core.remote_protocol import GetSessionProgressRequest

router = APIRouter()

_SESSION_PROGRESS_TIMEOUT = 5.0


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


@router.get("/executions/{execution_id}/session-progress")
async def get_session_progress(execution_id: UUID, request: Request) -> JSONResponse:
    registry = request.app.state.registry
    conn = registry.get_worker_by_execution(str(execution_id))

    if conn is None or conn.channel is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Execution not currently running"},
        )

    session_req = GetSessionProgressRequest(
        type="get_session_progress",
        request_id=str(uuid4()),
        execution_id=str(execution_id),
    )

    try:
        resp = await asyncio.wait_for(
            conn.channel.send_command(session_req),
            timeout=_SESSION_PROGRESS_TIMEOUT,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail={"error": "Worker timed out"})
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})

    return JSONResponse({
        "messages": resp.messages,
        "total_messages": resp.total_messages,
        "file_size_bytes": resp.file_size_bytes,
    })
