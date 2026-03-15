"""API: feature session endpoints for persistent Claude REPL."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.claude_repl import SpecReplSession, _QueueDone
from core.project_manager import ProjectManager
from web.queries import get_chat_session_by_context, get_chat_session_by_id
from web.routes.api.chat_images import get_image_media_type

router = APIRouter(prefix="/feature-sessions")

_INTERRUPTED = "[Request interrupted by user]"


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


def _build_feature_system_prompt(intent_md: str) -> str:
    return f"""You are helping design a software feature for this project.

## Project Intent
{intent_md}

## Your Role
Help the user think through this feature by asking clarifying questions one at a time.
Questions should be specific and concrete. Only one question per message. No preamble.

After sufficient clarification, when you have a clear picture of the feature,
produce the feature definition in this exact format:

## FEATURE READY
# Feature: <title>

## Description
<1-3 paragraph description of the feature>

## High-Level Specs
List each high-level spec as a numbered item. For each spec include:
- Title
- Order (integer, 1-based)
- Content: detailed description of what this spec should implement
- Dependencies: comma-separated indices of other specs this depends on (or "none")

Example format:
### 1. <Spec Title>
**Order:** 1
**Dependencies:** none
**Content:**
<detailed description>

### 2. <Spec Title>
**Order:** 2
**Dependencies:** 1
**Content:**
<detailed description>

Be specific about file paths, function names, and implementation requirements.
Read the codebase to understand current patterns before generating specs.

When the user says "done", "generate", or "go", produce the feature definition immediately."""


def _extract_feature_preview(text: str) -> dict[str, Any] | None:
    """Extract a preview dict from a ## FEATURE READY block."""
    marker = "## FEATURE READY"
    idx = text.find(marker)
    if idx == -1:
        return None
    block = text[idx + len(marker):].strip()

    title_m = re.search(r"^#\s+Feature:\s+(.+)$", block, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else "Untitled Feature"

    desc_m = re.search(
        r"##\s+Description\s*\n(.*?)(?=##\s+High-Level Specs|$)", block, re.DOTALL
    )
    description = desc_m.group(1).strip() if desc_m else ""

    specs_m = re.search(r"##\s+High-Level Specs\s*\n(.*?)$", block, re.DOTALL)
    specs_block = specs_m.group(1).strip() if specs_m else ""
    spec_count = len(re.findall(r"###\s+\d+\.", specs_block))

    return {"title": title, "description": description, "spec_count": spec_count, "raw": block}


class CreateSessionBody(BaseModel):
    project_id: UUID


class MessageBody(BaseModel):
    user_input: str
    image_id: UUID | None = None


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pool = request.app.state.pool

    # Return existing session if one already exists for this feature/project.
    existing = await get_chat_session_by_context(pool, body.project_id)
    if existing is not None:
        session_id = str(existing.id)
        if session_id not in request.app.state.feature_sessions:
            pm = ProjectManager(store)
            project = await pm.get_project(body.project_id)
            if project is None:
                raise HTTPException(
                    status_code=404, detail=f"Project {body.project_id} not found"
                )
            local_path = str(project.local_path)
            intent_md_path = Path(local_path) / "docs" / "INTENT.md"
            if not intent_md_path.exists():
                raise HTTPException(
                    status_code=400, detail="INTENT.md not found in project"
                )
            intent_md = intent_md_path.read_text()
            system_prompt = _build_feature_system_prompt(intent_md)
            session = SpecReplSession(
                task_id=str(body.project_id),
                system_prompt=system_prompt,
                cwd=local_path,
                history=_clean_history(list(existing.messages)),
            )
            request.app.state.feature_sessions[session_id] = session
        messages = [
            {"role": "user", "content": u, "assistant": a, "image_id": img}
            for u, a, img, _mt in existing.messages
        ]
        return JSONResponse({"session_id": session_id, "messages": messages})

    pm = ProjectManager(store)
    project = await pm.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        raise HTTPException(status_code=400, detail="INTENT.md not found in project")

    intent_md = intent_md_path.read_text()
    system_prompt = _build_feature_system_prompt(intent_md)

    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=str(body.project_id),
        system_prompt=system_prompt,
        cwd=local_path,
    )
    request.app.state.feature_sessions[session_id] = session

    await store.append_event(
        aggregate_id=UUID(session_id),
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "feature",
            "context_id": str(body.project_id),
            "context_type": "feature",
        },
    )

    return JSONResponse({"session_id": session_id, "messages": []})


async def _recover_session(session_id: str, request: Request) -> SpecReplSession | None:
    """Re-create a feature session from DB if it was lost due to server restart."""
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
    system_prompt = _build_feature_system_prompt(intent_md)
    session = SpecReplSession(
        task_id=str(project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        history=_clean_history(list(existing.messages)),
    )
    request.app.state.feature_sessions[session_id] = session
    return session


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.feature_sessions.get(session_id)
    if session is None:
        session = await _recover_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    store = request.app.state.store

    image_id_str: str | None = str(body.image_id) if body.image_id else None
    image_media_type: str | None = None
    if image_id_str:
        image_media_type = get_image_media_type(image_id_str)

    async def _on_complete(full_text: str) -> None:
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

    queue = await session.ask_detached(
        body.user_input, _on_complete, image_id_str, image_media_type
    )

    async def _stream() -> AsyncGenerator[str, None]:
        full_text = ""
        while True:
            chunk = await queue.get()
            if isinstance(chunk, _QueueDone):
                break
            if chunk is None:
                yield f"data: {json.dumps({'type': 'new_message'})}\n\n"
            else:
                full_text += chunk
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        feature_preview = _extract_feature_preview(full_text)

        yield f"data: {json.dumps({'type': 'done', 'feature': feature_preview})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    session: SpecReplSession | None = request.app.state.feature_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    await session.close()
    del request.app.state.feature_sessions[session_id]
    return JSONResponse({"ok": True})
