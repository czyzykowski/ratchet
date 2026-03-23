"""API: project chat session endpoints for free-form project conversation."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.claude_repl import SpecReplSession, _QueueDone
from core.models import Project
from core.project_manager import ProjectManager
from web.queries import get_chat_session_by_id, get_features_for_project, get_tasks_for_project

router = APIRouter(prefix="/project-chat-sessions")

_INTERRUPTED = "[Request interrupted by user]"
_PROJECT_CHAT_ALLOWED_TOOLS = "Read,Glob,Grep,Bash(git log:*,git show:*,git diff:*)"


def _clean_history(
    messages: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (u, a, img, mt)
        for u, a, img, mt in messages
        if a and _INTERRUPTED not in u
    ]


def _build_project_chat_prompt(
    project: Project,
    intent_md: str,
    claude_md: str,
    recent_commits: list[str],
    task_summaries: list[dict[str, Any]],
    feature_summaries: list[dict[str, Any]],
) -> str:
    sections: list[str] = []

    sections.append(f"## Project Intent\n{intent_md}")

    if claude_md:
        sections.append(f"## Project Guidelines\n{claude_md}")

    if recent_commits:
        commit_lines = "\n".join(f"- {c}" for c in recent_commits)
        sections.append(f"## Recent Activity\n{commit_lines}")

    if task_summaries:
        by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in task_summaries:
            by_status[task["status"]].append(task)
        task_rows: list[str] = ["| Title | Feature |", "| ----- | ------- |"]
        for status in sorted(by_status.keys()):
            task_rows.append(f"| **{status}** | |")
            for task in by_status[status]:
                feature = task.get("feature_title") or ""
                task_rows.append(f"| {task['title']} | {feature} |")
        sections.append("## Tasks\n" + "\n".join(task_rows))
    else:
        sections.append("## Tasks\n*No tasks yet.*")

    if feature_summaries:
        feat_rows: list[str] = ["| Title | Specs | Compiled |", "| ----- | ----- | -------- |"]
        for feat in feature_summaries:
            feat_rows.append(
                f"| {feat['title']} | {feat['spec_count']} | {feat['compiled_count']} |"
            )
        sections.append("## Features\n" + "\n".join(feat_rows))
    else:
        sections.append("## Features\n*No features yet.*")

    capabilities = (
        f"You are a project assistant for {project.name}. You have full read access to the "
        "codebase and project data. You can create tasks, create features, and modify task "
        "metadata when the user asks."
    )
    sections.append(f"## Capabilities\n{capabilities}")

    return "\n\n".join(sections)


async def _gather_project_context(
    project: Project, pool: object
) -> tuple[str, str, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Gather intent_md, claude_md, recent_commits, task_summaries, feature_summaries."""
    local_path = str(project.local_path)

    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    intent_md = intent_md_path.read_text() if intent_md_path.exists() else ""

    claude_md_path = Path(local_path) / "CLAUDE.md"
    claude_md = claude_md_path.read_text() if claude_md_path.exists() else ""

    recent_commits: list[str] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            "--oneline",
            "-20",
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            recent_commits = [
                line for line in stdout.decode().splitlines() if line.strip()
            ]
    except Exception:
        pass

    async with pool.connection() as conn:  # type: ignore[attr-defined]
        task_summaries = await get_tasks_for_project(conn, project.id)
        feature_summaries = await get_features_for_project(conn, project.id)

    return intent_md, claude_md, recent_commits, task_summaries, feature_summaries


class CreateSessionBody(BaseModel):
    project_id: UUID


class MessageBody(BaseModel):
    user_input: str


@router.post("")
async def create_session(body: CreateSessionBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    pool = request.app.state.pool

    pm = ProjectManager(store)
    project = await pm.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    local_path = str(project.local_path)
    intent_md_path = Path(local_path) / "docs" / "INTENT.md"
    if not intent_md_path.exists():
        raise HTTPException(status_code=400, detail="INTENT.md not found in project")

    intent_md, claude_md, recent_commits, task_summaries, feature_summaries = (
        await _gather_project_context(project, pool)
    )
    system_prompt = _build_project_chat_prompt(
        project, intent_md, claude_md, recent_commits, task_summaries, feature_summaries
    )

    session_id = str(uuid4())
    session = SpecReplSession(
        task_id=str(body.project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        allowed_tools=_PROJECT_CHAT_ALLOWED_TOOLS,
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

    intent_md, claude_md, recent_commits, task_summaries, feature_summaries = (
        await _gather_project_context(project, pool)
    )
    system_prompt = _build_project_chat_prompt(
        project, intent_md, claude_md, recent_commits, task_summaries, feature_summaries
    )
    session = SpecReplSession(
        task_id=str(project_id),
        system_prompt=system_prompt,
        cwd=local_path,
        history=_clean_history(list(existing.messages)),
        allowed_tools=_PROJECT_CHAT_ALLOWED_TOOLS,
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
