"""API: task endpoints under /api/tasks."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.execution_manager import ExecutionManager
from core.models import QAExchange
from core.project_manager import ProjectManager
from core.qa_manager import get_pending_question, get_qa_history
from core.spec_manager import SpecManager
from core.state_machine import InvalidTransitionError, TaskStateMachine
from core.task_manager import TaskManager
from web.sse import broadcast_task_updated

router = APIRouter()


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    task_dict = task.model_dump(mode="json")

    # Specs
    spec_manager = SpecManager(store)
    spec_lineage = await spec_manager.get_spec_lineage(task_id)
    specs_data = [
        {
            "id": str(s.id),
            "task_id": str(s.task_id),
            "previous_spec_id": str(s.previous_spec_id) if s.previous_spec_id else None,
            "content": s.content,
            "created_at": s.created_at.isoformat(),
        }
        for s in spec_lineage
    ]

    # Executions
    em = ExecutionManager(store, "")
    executions = await em.get_execution_history(task_id)
    executions_data = [
        {
            "id": str(e.id),
            "task_id": str(e.task_id),
            "spec_id": str(e.spec_id),
            "status": e.status,
            "failure_reason": e.failure_reason,
            "branch_name": e.branch_name,
            "started_at": e.started_at.isoformat(),
            "completed_at": e.completed_at.isoformat() if e.completed_at else None,
        }
        for e in executions
    ]

    # QA failure reason from latest BLOCKED transition
    task_events = await store.get_events(task_id, "task")
    qa_failure: str | None = None
    for event in reversed(task_events):
        if (
            event.event_type == ev.TASK_STATUS_CHANGED
            and event.payload.get("to_status") == ev.BLOCKED
        ):
            qa_failure = event.payload.get("failure_reason")
            break

    # Baseline QA failure (pending, not overridden by force-execute)
    baseline_qa_failure: str | None = None
    last_failed_seq: int | None = None
    last_cleared_seq: int | None = None
    failure_output: str | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
            failure_output = event.payload.get("failure_output")
        elif event.event_type in (ev.TASK_BASELINE_QA_RETRY, ev.TASK_FORCE_EXECUTE):
            last_cleared_seq = max(last_cleared_seq or 0, event.sequence)
    if (
        last_failed_seq is not None
        and (last_cleared_seq is None or last_failed_seq > last_cleared_seq)
    ):
        baseline_qa_failure = failure_output

    # Project name
    pm = ProjectManager(store)
    project = await pm.get_project(task.project_id)
    project_name = project.name if project is not None else None

    return JSONResponse(
        {
            "task": task_dict,
            "project_name": project_name,
            "specs": specs_data,
            "executions": executions_data,
            "dependencies": task_dict.get("depends_on", []),
            "qa_failure": qa_failure,
            "baseline_qa_failure": baseline_qa_failure,
        }
    )


async def _task_status_generator(
    store: Any, task_id: UUID, request: Any
) -> AsyncGenerator[str, None]:
    """Async generator that emits SSE events when task status changes."""
    last_status: str | None = None
    task_manager = TaskManager(store)
    try:
        while True:
            if await request.is_disconnected():
                break
            task = await task_manager.get_task(task_id)
            if task is not None:
                current_status = task.status
                if current_status != last_status:
                    last_status = current_status
                    data = json.dumps({"status": current_status})
                    yield f"data: {data}\n\n"
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        pass


@router.get("/tasks/{task_id}/events")
async def task_sse_events(task_id: UUID, request: Request) -> StreamingResponse:
    store = request.app.state.store
    return StreamingResponse(
        content=_task_status_generator(store, task_id, request),
        media_type="text/event-stream",
    )


class CreateTaskBody(BaseModel):
    project_id: str
    title: str


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    project_id = UUID(body.project_id)
    task_manager = TaskManager(store)

    task = await task_manager.create_task(project_id, body.title)
    return JSONResponse({"task": task.model_dump(mode="json")}, status_code=201)


class UpdateTaskBody(BaseModel):
    title: str


@router.patch("/tasks/{task_id}")
async def update_task_title(task_id: UUID, body: UpdateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store

    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    task_events = await store.get_events(task_id, "task")
    if not task_events:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_TITLE_CHANGED,
        payload={"title": body.title},
    )

    broadcast_task_updated(request.app)
    return JSONResponse({"id": str(task_id), "title": body.title})


class AssignSpecBody(BaseModel):
    content: str


@router.post("/tasks/{task_id}/spec")
async def assign_spec(task_id: UUID, body: AssignSpecBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    spec_manager = SpecManager(store)
    task_manager = TaskManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    allowed = {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION}
    if current_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot assign spec to task in status {current_status!r}",
        )

    current_spec = await spec_manager.get_current_spec(task_id)
    previous_spec_id = current_spec.id if current_spec is not None else None

    spec = await spec_manager.create_spec(task_id, body.content, previous_spec_id)
    await spec_manager.assign_spec(task_id, spec.id)

    try:
        if current_status == ev.READY_FOR_SPEC:
            await state_machine.transition(task_id, ev.SPEC_QA)
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        elif current_status in (ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION):
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    task = await task_manager.get_task(task_id)
    spec_data = {
        "id": str(spec.id),
        "task_id": str(spec.task_id),
        "previous_spec_id": str(spec.previous_spec_id) if spec.previous_spec_id else None,
        "content": spec.content,
        "created_at": spec.created_at.isoformat(),
    }
    return JSONResponse({"task": task.model_dump(mode="json") if task else None, "spec": spec_data})


@router.post("/tasks/{task_id}/reset")
async def reset_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    task_manager = TaskManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.BLOCKED:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not blocked")

    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)

    task = await task_manager.get_task(task_id)
    return JSONResponse({"task": task.model_dump(mode="json") if task else None})


class DeployRequest(BaseModel):
    skip_merge: bool = False


@router.post("/tasks/{task_id}/deploy")
async def deploy_task(
    task_id: UUID, request: Request, body: DeployRequest = DeployRequest()
) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    pm = ProjectManager(store)
    task_manager = TaskManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.READY_FOR_DEPLOYMENT:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not ready for deployment",
        )

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    project = await pm.get_project(task.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {task.project_id} not found")

    execution_events = await store.get_events(task_id, "task_executions")
    branch_name: str | None = None
    for event in reversed(execution_events):
        if event.event_type == ev.EXECUTION_STARTED:
            bn = event.payload.get("branch_name")
            if bn:
                branch_name = bn
            break

    if branch_name is None:
        raise HTTPException(status_code=400, detail="No execution branch found for this task")

    if not body.skip_merge:
        local_path = str(project.local_path)
        title = task.title
        target_branch = "develop"

        try:
            subprocess.run(
                ["git", "checkout", target_branch],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "merge", "--squash", branch_name],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
            commit_msg = f"feat: {title} (task/{task_id})"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
            try:
                subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                pass
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode().strip()
            stdout = (exc.stdout or b"").decode().strip()
            detail = stderr or stdout or f"git command failed with exit code {exc.returncode}"
            raise HTTPException(status_code=400, detail=detail)

    await state_machine.transition(task_id, ev.DEPLOYED)

    task = await task_manager.get_task(task_id)
    return JSONResponse({"task": task.model_dump(mode="json") if task else None})


@router.post("/tasks/{task_id}/retry-baseline-qa")
async def retry_baseline_qa(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.READY_FOR_IMPLEMENTATION:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not in ready_for_implementation status",
        )

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_BASELINE_QA_RETRY,
        payload={},
    )

    task = await task_manager.get_task(task_id)
    return JSONResponse({"task": task.model_dump(mode="json") if task else None})


@router.post("/tasks/{task_id}/force-execute")
async def force_execute_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.READY_FOR_IMPLEMENTATION:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not in ready_for_implementation status",
        )

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_FORCE_EXECUTE,
        payload={},
    )

    task = await task_manager.get_task(task_id)
    return JSONResponse({"task": task.model_dump(mode="json") if task else None})


# ── Q&A Endpoints ─────────────────────────────────────────────────────────────


def _serialize_qa_exchange(exchange: QAExchange) -> dict[str, Any]:
    return {
        "question_index": exchange.question_index,
        "question": exchange.question,
        "answer": exchange.answer,
        "execution_id": str(exchange.execution_id),
        "asked_at": exchange.asked_at.isoformat(),
        "answered_at": exchange.answered_at.isoformat() if exchange.answered_at else None,
        "answered_by": exchange.answered_by,
    }


@router.get("/tasks/{task_id}/qa")
async def get_task_qa(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    history = await get_qa_history(store, task_id)
    pending = await get_pending_question(store, task_id)

    return JSONResponse({
        "history": [_serialize_qa_exchange(x) for x in history],
        "pending": _serialize_qa_exchange(pending) if pending is not None else None,
    })


class AnswerBody(BaseModel):
    answer: str
    question_index: int


@router.post("/tasks/{task_id}/answer")
async def answer_pending_question(
    task_id: UUID, body: AnswerBody, request: Request
) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.WAITING_FOR_INPUT:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not in waiting_for_input status",
        )

    pending = await get_pending_question(store, task_id)
    if pending is None or pending.question_index != body.question_index:
        raise HTTPException(
            status_code=400,
            detail=f"No pending question at index {body.question_index}",
        )

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_INPUT_PROVIDED,
        payload={
            "answer": body.answer,
            "question_index": body.question_index,
            "answered_by": "spa",
        },
    )

    answered = QAExchange(
        question_index=pending.question_index,
        question=pending.question,
        answer=body.answer,
        execution_id=pending.execution_id,
        asked_at=pending.asked_at,
        answered_at=None,
        answered_by="spa",
    )
    return JSONResponse({"exchange": _serialize_qa_exchange(answered)})


# ── Spec Chat ─────────────────────────────────────────────────────────────────

_SPEC_ROLE_PROMPT = """\
You are helping design a software task for the Ratchet project.

## Project Intent
{intent_md}

## Task
Title: {task_title}

## Your Role
Help the user think through this task by asking clarifying questions one at a time.
Questions should be specific and concrete — multiple choice where possible,
open-ended when necessary. Only one question per message. No preamble before the question.

After sufficient clarification, describe your understanding of the task in chunks of 200-300 words,
asking after each chunk whether it looks right.
Keep to chunked format even when the picture is clear.

When you have gathered enough information and confirmed your understanding with the user,
produce the spec in this exact format:

## SPEC READY
# Spec N: <title>

## Objective
...

## Success Criteria
- [ ] ...

## Out of Scope
...

## Technical Context
...

## Tasks
- [ ] ...

## Assumptions
...

## Verification Commands
```bash
...
```

## What Exists After This Spec

...

Use the standard Ratchet spec format exactly as shown. Be specific about file paths,
function names, and test requirements. Read the codebase to understand current patterns
before generating the spec.

## User's Opening Description

{user_description}"""


def _build_initial_spec_prompt(intent_md: str, task_title: str, user_description: str) -> str:
    return _SPEC_ROLE_PROMPT.format(
        intent_md=intent_md,
        task_title=task_title,
        user_description=user_description,
    )


