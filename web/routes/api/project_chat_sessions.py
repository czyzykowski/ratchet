"""API: project chat session endpoints for free-form project conversation."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.claude_repl import SpecReplSession, _QueueDone
from core.project_manager import ProjectManager
from web.queries import get_chat_session_by_id

router = APIRouter(prefix="/project-chat-sessions")

_INTERRUPTED = "[Request interrupted by user]"


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


def _build_project_chat_system_prompt(intent_md: str) -> str:
    return f"""You are a helpful assistant for this software project.

## Project Intent
{intent_md}

## Your Role
Help the user with any questions or tasks related to this project. You can discuss
architecture, answer questions about the codebase, help plan features, review code,
or assist with any other project-related topics.

Be concise and direct. Read the codebase when needed to give accurate answers."""


class CreateSessionBody(BaseModel):
    project_id: UUID


class MessageBody(BaseModel):
    user_input: str


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store

    pm = ProjectManager(store)
    project = await pm.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        raise HTTPException(status_code=400, detail="INTENT.md not found in project")

    intent_md = intent_md_path.read_text()
    system_prompt = _build_project_chat_system_prompt(intent_md)

    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=str(body.project_id),
        system_prompt=system_prompt,
        cwd=local_path,
    )
    request.app.state.project_chat_sessions[session_id] = session

    await store.append_event(
        aggregate_id=UUID(session_id),
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "project_chat",
            "context_id": str(body.project_id),
            "context_type": "project",
        },
    )

    return JSONResponse({"session_id": session_id, "messages": []})


@router.get("")
async def list_sessions(project_id: UUID, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, created_at FROM current_chat_sessions"
                " WHERE context_id = %s AND session_type = 'project_chat'"
                " ORDER BY created_at DESC",
                (str(project_id),),
            )
            rows = await cur.fetchall()
    result = [
        {"session_id": str(row[0]), "created_at": row[1].isoformat()}
        for row in rows
    ]
    return JSONResponse(result)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    existing = await get_chat_session_by_id(pool, UUID(session_id))
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    messages = [
        {"role": "user", "content": u, "assistant": a, "image_id": img}
        for u, a, img, _mt in existing.messages
    ]
    return JSONResponse({"session_id": session_id, "messages": messages})


async def _recover_session(session_id: str, request: Request) -> SpecReplSession | None:
    """Re-create a project chat session from DB if lost due to server restart."""
    pool = request.app.state.pool
    store = request.app.state.store
    existing = await get_chat_session_by_id(pool, UUID(session_id))
    if existing is None:
        return None
    project_id = existing.context_id
    pm = ProjectManager(store)
    project = await pm.get_project(project_id)
    if project is None:
        return None
    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        return None
    intent_md = intent_md_path.read_text()
    system_prompt = _build_project_chat_system_prompt(intent_md)
    session = SpecReplSession(
        task_id=str(project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        history=_clean_history(list(existing.messages)),
    )
    request.app.state.project_chat_sessions[session_id] = session
    return session


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.project_chat_sessions.get(session_id)
    if session is None:
        session = await _recover_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    store = request.app.state.store

    async def _on_complete(full_text: str) -> None:
        await store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_MESSAGE_ADDED,
            payload={
                "user_input": body.user_input,
                "assistant_text": full_text,
                "image_id": None,
                "image_media_type": None,
            },
        )

    queue = await session.ask_detached(body.user_input, _on_complete)

    async def _stream() -> AsyncGenerator[str, None]:
        while True:
            chunk = await queue.get()
            if isinstance(chunk, _QueueDone):
                break
            if chunk is None:
                yield f"data: {json.dumps({'type': 'new_message'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    session: SpecReplSession | None = request.app.state.project_chat_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    await session.close()
    del request.app.state.project_chat_sessions[session_id]
    return JSONResponse({"ok": True})
