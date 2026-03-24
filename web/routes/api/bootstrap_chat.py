"""API: bootstrap chat session endpoints for project brainstorming."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.claude_repl import SpecReplSession, _QueueDone
from web.action_executor import execute_action
from web.action_parser import parse_action_blocks, replace_action_block
from web.queries import get_bootstrap_sessions, get_chat_session_by_id

router = APIRouter(prefix="/bootstrap-chat-sessions")

_INTERRUPTED = "[Request interrupted by user]"
_BOOTSTRAP_ALLOWED_TOOLS = "Read,Glob,Grep,Bash,Write"


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


def _build_bootstrap_system_prompt() -> str:
    role = (
        "You are a project bootstrapping assistant. Your goal is to guide the user from a "
        "vague idea to a concrete, scaffolded project through structured conversation.\n\n"
        "**Interaction pattern:**\n"
        "- Ask ONE question at a time. Never ask multiple questions in a single turn.\n"
        "- Prefer multiple choice options when exploring preferences"
        " (tech stack, architecture, etc).\n"
        "- Present design summaries in 200-300 word chunks. Always include an approval checkpoint "
        "before proceeding to the next phase — ask 'Does this look right? Shall I continue?'\n"
        "- Never scaffold or create files until the user has approved the full design.\n\n"
        "**Conversation phases:**\n"
        "1. **Explore the idea** — understand what the user wants to build, who it's for, and "
        "what problem it solves. Ask clarifying questions one at a time.\n"
        "2. **Discover tech stack** — propose options suited to the idea."
        " Present as multiple choice. "
        "Confirm choices with approval checkpoint.\n"
        "3. **Refine design** — describe the architecture, key components, and data flow"
        " in 200-300 "
        "words. Include an approval checkpoint before proceeding.\n"
        "4. **Scaffold with action blocks** — only after full design approval,"
        " use action blocks to "
        "register the project and create initial tasks.\n\n"
        "**Available action blocks:**\n\n"
        "Register a new project:\n"
        "```action\n"
        '{"action": "register_project", "name": "my-project", "path": "/path/to/project"}\n'
        "```\n\n"
        "Create a task for the project:\n"
        "```action\n"
        '{"action": "create_task", "title": "Task title here"}\n'
        "```\n\n"
        "Check the status of a task:\n"
        "```action\n"
        '{"action": "check_task_status", "task_id": "<task_id>"}\n'
        "```\n\n"
        "**Rules:**\n"
        "- Only use action blocks after the user has approved the full design.\n"
        "- Explain what each action will do before executing it.\n"
        "- Use `register_project` before `create_task` — you need the project to exist first.\n"
        "- One question at a time. Multiple choice preferred."
        " Approval checkpoints before each phase."
    )
    return role


class CreateSessionBody(BaseModel):
    working_directory: str | None = None


class MessageBody(BaseModel):
    user_input: str


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store

    system_prompt = _build_bootstrap_system_prompt()
    cwd = body.working_directory or "/tmp"

    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=session_id,
        system_prompt=system_prompt,
        cwd=cwd,
        allowed_tools=_BOOTSTRAP_ALLOWED_TOOLS,
    )
    request.app.state.bootstrap_chat_sessions[session_id] = session

    await store.append_event(
        aggregate_id=UUID(session_id),
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "bootstrap",
            "context_id": None,
            "context_type": "bootstrap",
        },
    )

    return JSONResponse({"session_id": session_id, "messages": []})


@router.get("")
async def list_sessions(request: Request) -> JSONResponse:
    pool = request.app.state.pool
    sessions = await get_bootstrap_sessions(pool)
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
    """Re-create a bootstrap session from DB if lost due to server restart."""
    pool = request.app.state.pool
    existing = await get_chat_session_by_id(pool, UUID(session_id))
    if existing is None:
        return None

    system_prompt = _build_bootstrap_system_prompt()

    # Recover working_directory from the original CHAT_SESSION_CREATED event
    store = request.app.state.store
    session_events = await store.get_events(UUID(session_id), "chat_session")
    cwd = "/tmp"
    for e in session_events:
        if e.event_type == ev.CHAT_SESSION_CREATED:
            stored_cwd = e.payload.get("working_directory")
            if stored_cwd:
                cwd = stored_cwd
            break

    session = SpecReplSession(
        task_id=session_id,
        system_prompt=system_prompt,
        cwd=cwd,
        history=_clean_history(list(existing.messages)),
        allowed_tools=_BOOTSTRAP_ALLOWED_TOOLS,
    )
    request.app.state.bootstrap_chat_sessions[session_id] = session
    return session


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageBody, request: Request
) -> StreamingResponse:
    session: SpecReplSession | None = request.app.state.bootstrap_chat_sessions.get(
        session_id
    )
    if session is None:
        session = await _recover_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    store = request.app.state.store

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
            result = await execute_action(_parsed, store, None)
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
    session: SpecReplSession | None = request.app.state.bootstrap_chat_sessions.get(
        session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    await session.close()
    del request.app.state.bootstrap_chat_sessions[session_id]
    return JSONResponse({"ok": True})
