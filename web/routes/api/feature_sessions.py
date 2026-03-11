"""API: feature session endpoints for persistent Claude REPL."""

from __future__ import annotations

import json
from typing import Any
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core.claude_repl import SpecReplSession
from core.project_manager import ProjectManager

router = APIRouter(prefix="/feature-sessions")


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

    desc_m = re.search(r"##\s+Description\s*\n(.*?)(?=##\s+High-Level Specs|$)", block, re.DOTALL)
    description = desc_m.group(1).strip() if desc_m else ""

    specs_m = re.search(r"##\s+High-Level Specs\s*\n(.*?)$", block, re.DOTALL)
    specs_block = specs_m.group(1).strip() if specs_m else ""
    spec_count = len(re.findall(r"###\s+\d+\.", specs_block))

    return {"title": title, "description": description, "spec_count": spec_count, "raw": block}


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
    system_prompt = _build_feature_system_prompt(intent_md)

    session = SpecReplSession(
        task_id=str(body.project_id),
        system_prompt=system_prompt,
        cwd=local_path,
    )

    session_id = str(uuid4())
    request.app.state.feature_sessions[session_id] = session
    return JSONResponse({"session_id": session_id})


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.feature_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    async def _stream() -> AsyncGenerator[str, None]:
        full_text = ""
        async for chunk in session.ask(body.user_input):
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
