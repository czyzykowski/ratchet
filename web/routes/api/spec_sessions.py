"""API: spec session endpoints for persistent Claude REPL."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core.claude_repl import SpecReplSession
from core.project_manager import ProjectManager
from core.task_manager import TaskManager
from web.routes.api.tasks import _build_initial_spec_prompt

router = APIRouter(prefix="/spec-sessions")


class CreateSessionBody(BaseModel):
    task_id: UUID


class MessageBody(BaseModel):
    user_input: str


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)

    task = await task_manager.get_task(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {body.task_id} not found")

    pm = ProjectManager(store)
    project = await pm.get_project(task.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        raise HTTPException(status_code=400, detail="INTENT.md not found in project")

    intent_md = intent_md_path.read_text()
    task_title: str = task.title

    system_prompt = _build_initial_spec_prompt(intent_md, task_title, task_title)
    session = SpecReplSession(
        task_id=str(body.task_id),
        system_prompt=system_prompt,
        cwd=local_path,
    )

    session_id = str(uuid4())
    request.app.state.spec_sessions[session_id] = session
    return JSONResponse({"session_id": session_id})


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.spec_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    async def _stream() -> AsyncGenerator[str, None]:
        full_text = ""
        async for chunk in session.ask(body.user_input):
            full_text += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        spec_content: str | None = None
        if "## SPEC READY" in full_text:
            idx = full_text.find("## SPEC READY")
            spec_content = full_text[idx + len("## SPEC READY") :].strip()

        yield f"data: {json.dumps({'type': 'done', 'spec': spec_content})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    session: SpecReplSession | None = request.app.state.spec_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    await session.close()
    del request.app.state.spec_sessions[session_id]
    return JSONResponse({"ok": True})
