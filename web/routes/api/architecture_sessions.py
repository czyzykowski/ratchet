"""API: architecture session endpoints for codebase architecture analysis."""

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
from core.models import Project
from core.project_manager import ProjectManager
from web.action_executor import execute_action
from web.action_parser import parse_action_blocks, replace_action_block
from web.queries import get_architecture_sessions_for_project, get_chat_session_by_id

router = APIRouter(prefix="/architecture-sessions")

_INTERRUPTED = "[Request interrupted by user]"
_ARCHITECTURE_ALLOWED_TOOLS = (
    "Read,Glob,Grep,Bash(git log:*,git show:*,git diff:*,find:*,wc:*)"
)


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


def _build_architecture_system_prompt(
    project: Project,
    intent_md: str,
    claude_md: str,
    scope: str | None,
) -> str:
    sections: list[str] = []

    sections.append(f"## Project Intent\n{intent_md}")

    if claude_md:
        sections.append(f"## Project Guidelines\n{claude_md}")

    if scope:
        sections.append(f"## Analysis Scope\n{scope}")

    role = (
        f"You are an architecture analyst for {project.name}. Your role is to:\n"
        "- Identify coupling between modules and suggest improvements\n"
        "- Analyze module boundaries and separation of concerns\n"
        "- Evaluate dependency graphs and highlight problematic patterns\n"
        "- Suggest refactoring opportunities to improve maintainability\n"
        "- Propose tasks for concrete architectural improvements\n\n"
        "You have read access to the full codebase. Use it to understand the structure before "
        "making recommendations."
    )
    sections.append(f"## Role\n{role}")

    write_actions = (
        "To propose an architectural improvement task, output a fenced action block:\n\n"
        "```action\n"
        '{"action": "create_task", "title": "Task title here"}\n'
        "```\n\n"
        "Rules:\n"
        "- Explain the architectural problem before proposing a task\n"
        "- Only propose tasks for concrete, actionable improvements\n"
        "- Confirm with the user before creating multiple tasks at once"
    )
    sections.append(f"## Write Actions\n{write_actions}")

    return "\n\n".join(sections)


class CreateSessionBody(BaseModel):
    project_id: UUID
    scope: str | None = None


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

    claude_md_path = Path(local_path) / "CLAUDE.md"
    claude_md = claude_md_path.read_text() if claude_md_path.exists() else ""

    system_prompt = _build_architecture_system_prompt(
        project, intent_md, claude_md, body.scope
    )

    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=str(body.project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        allowed_tools=_ARCHITECTURE_ALLOWED_TOOLS,
    )
    request.app.state.architecture_sessions[session_id] = session

    payload: dict[str, object] = {
        "session_type": "architecture",
        "context_id": str(body.project_id),
        "context_type": "project",
    }
    if body.scope is not None:
        payload["scope"] = body.scope

    await store.append_event(
        aggregate_id=UUID(session_id),
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload=payload,
    )

    return JSONResponse({"session_id": session_id, "messages": []})


@router.get("")
async def list_sessions(project_id: UUID, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    sessions = await get_architecture_sessions_for_project(pool, project_id)
    result = [
        {"session_id": str(s.id), "created_at": s.created_at.isoformat()}
        for s in sessions
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
    """Re-create an architecture session from DB if lost due to server restart."""
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
    claude_md_path = Path(local_path) / "CLAUDE.md"
    claude_md = claude_md_path.read_text() if claude_md_path.exists() else ""

    # Recover scope from the original CHAT_SESSION_CREATED event
    scope: str | None = None
    session_events = await store.get_events(UUID(session_id), "chat_session")
    for e in session_events:
        if e.event_type == ev.CHAT_SESSION_CREATED:
            scope = e.payload.get("scope")
            break

    system_prompt = _build_architecture_system_prompt(project, intent_md, claude_md, scope)
    session = SpecReplSession(
        task_id=str(project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        history=_clean_history(list(existing.messages)),
        allowed_tools=_ARCHITECTURE_ALLOWED_TOOLS,
    )
    request.app.state.architecture_sessions[session_id] = session
    return session


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.architecture_sessions.get(session_id)
    if session is None:
        session = await _recover_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    store = request.app.state.store
    project_id = UUID(session.task_id)

    async def _persist(text: str) -> None:
        await store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_MESSAGE_ADDED,
            payload={
                "user_input": body.user_input,
                "assistant_text": text,
                "image_id": None,
                "image_media_type": None,
            },
        )

    async def _noop(_: str) -> None:
        pass

    queue = await session.ask_detached(body.user_input, _noop)

    async def _stream() -> AsyncGenerator[str, None]:
        accumulated = ""
        while True:
            chunk = await queue.get()
            if isinstance(chunk, _QueueDone):
                break
            if chunk is None:
                yield f"data: {json.dumps({'type': 'new_message'})}\n\n"
                accumulated = ""
            else:
                accumulated += chunk
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        # Detect and execute action blocks in accumulated text
        action_blocks = parse_action_blocks(accumulated)
        modified = accumulated
        for _parsed in action_blocks:
            result = await execute_action(_parsed, store, project_id)
            confirmation = result.message if result.success else f"⚠ {result.error}"
            fresh = parse_action_blocks(modified)
            if fresh:
                modified = replace_action_block(modified, fresh[0], confirmation)
            event_payload = {
                "type": "action_executed",
                "action": result.action,
                "result": {
                    "success": result.success,
                    "message": result.message,
                    "entity_id": result.entity_id,
                    "error": result.error,
                },
            }
            yield f"data: {json.dumps(event_payload)}\n\n"

        await _persist(modified)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    session: SpecReplSession | None = request.app.state.architecture_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    await session.close()
    del request.app.state.architecture_sessions[session_id]
    return JSONResponse({"ok": True})
