"""API: spec session endpoints for persistent Claude REPL."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.claude_repl import SpecReplSession
from core.project_manager import ProjectManager
from core.task_manager import TaskManager
from web.queries import get_chat_session_by_context, get_chat_session_by_id
from web.routes.api.chat_images import get_image_media_type
from web.routes.api.tasks import _build_initial_spec_prompt

_INTERRUPTED = "[Request interrupted by user]"


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


router = APIRouter(prefix="/spec-sessions")


class CreateSessionBody(BaseModel):
    task_id: UUID


class MessageBody(BaseModel):
    user_input: str
    image_id: UUID | None = None


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pool = request.app.state.pool

    # Return existing session if one already exists for this task.
    existing = await get_chat_session_by_context(pool, body.task_id)
    if existing is not None:
        session_id = str(existing.id)
        if session_id not in request.app.state.spec_sessions:
            task_manager = TaskManager(store)
            task = await task_manager.get_task(body.task_id)
            if task is None:
                raise HTTPException(
                    status_code=404, detail=f"Task {body.task_id} not found"
                )
            pm = ProjectManager(store)
            project = await pm.get_project(task.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            local_path = str(project.local_path)
            intent_md_path = Path(local_path) / "docs" / "INTENT.md"
            if not intent_md_path.exists():
                raise HTTPException(
                    status_code=400, detail="INTENT.md not found in project"
                )
            intent_md = intent_md_path.read_text()
            system_prompt = _build_initial_spec_prompt(
                intent_md, task.title, task.title
            )
            session = SpecReplSession(
                task_id=str(body.task_id),
                system_prompt=system_prompt,
                cwd=local_path,
                history=_clean_history(list(existing.messages)),
            )
            request.app.state.spec_sessions[session_id] = session
        messages = [
            {"role": "user", "content": u, "assistant": a, "image_id": img}
            for u, a, img, _mt in existing.messages
        ]
        return JSONResponse({"session_id": session_id, "messages": messages})

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
    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=str(body.task_id),
        system_prompt=system_prompt,
        cwd=local_path,
    )
    request.app.state.spec_sessions[session_id] = session

    await store.append_event(
        aggregate_id=UUID(session_id),
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "spec",
            "context_id": str(body.task_id),
            "context_type": "task",
        },
    )

    return JSONResponse({"session_id": session_id, "messages": []})


async def _recover_session(session_id: str, request: Request) -> SpecReplSession | None:
    """Re-create a session from DB if it was lost due to server restart."""
    pool = request.app.state.pool
    store = request.app.state.store
    existing = await get_chat_session_by_id(pool, UUID(session_id))
    if existing is None:
        return None
    task_id = existing.context_id
    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    if task is None:
        return None
    pm = ProjectManager(store)
    project = await pm.get_project(task.project_id)
    if project is None:
        return None
    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        return None
    intent_md = intent_md_path.read_text()
    system_prompt = _build_initial_spec_prompt(intent_md, task.title, task.title)
    session = SpecReplSession(
        task_id=str(task_id),
        system_prompt=system_prompt,
        cwd=local_path,
        history=_clean_history(list(existing.messages)),
    )
    request.app.state.spec_sessions[session_id] = session
    return session


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.spec_sessions.get(session_id)
    if session is None:
        session = await _recover_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    store = request.app.state.store

    image_id_str: str | None = str(body.image_id) if body.image_id else None
    image_media_type: str | None = None
    if image_id_str:
        image_media_type = get_image_media_type(image_id_str)

    async def _stream() -> AsyncGenerator[str, None]:
        full_text = ""
        async for chunk in session.ask(body.user_input, image_id_str, image_media_type):
            if chunk is None:
                yield f"data: {json.dumps({'type': 'new_message'})}\n\n"
            else:
                full_text += chunk
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        spec_content: str | None = None
        if "## SPEC READY" in full_text:
            idx = full_text.find("## SPEC READY")
            spec_content = full_text[idx + len("## SPEC READY"):].strip()

        await store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_MESSAGE_ADDED,
            payload={
                "user_input": body.user_input,
                "assistant_text": full_text,
                "image_id": image_id_str,
                "image_media_type": image_media_type,
            },
        )

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
