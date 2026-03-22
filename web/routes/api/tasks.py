"""API: task endpoints under /api/tasks."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import events as ev
from core.models import QAExchange
from core.project_manager import ProjectManager
from core.qa_manager import get_pending_question, get_qa_history
from core.spec_manager import SpecManager
from core.state_machine import InvalidTransitionError, TaskStateMachine
from core.task_manager import TaskManager
from web import queries
from web.sse import broadcast_task_updated

router = APIRouter()


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        detail = await queries.get_task_detail(conn, task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return JSONResponse(detail)


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
    required_capabilities: list[str] | None = None


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    project_id = UUID(body.project_id)
    task_manager = TaskManager(store)
    pm = ProjectManager(store)

    if body.required_capabilities is not None:
        task = await task_manager.create_task(
            project_id,
            body.title,
            required_capabilities=body.required_capabilities,
            project_capabilities=[],
        )
    else:
        project = await pm.get_project(project_id)
        project_capabilities = project.required_capabilities if project is not None else []
        task = await task_manager.create_task(
            project_id, body.title, project_capabilities=project_capabilities
        )
    return JSONResponse({"task": task.model_dump(mode="json")}, status_code=201)


class UpdateTaskBody(BaseModel):
    title: str | None = None
    required_capabilities: list[str] | None = None


@router.patch("/tasks/{task_id}")
async def update_task_title(task_id: UUID, body: UpdateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_manager = TaskManager(store)

    task_events = await store.get_events(task_id, "task")
    if not task_events:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_TITLE_CHANGED,
            payload={"title": body.title},
        )

    if body.required_capabilities is not None:
        await task_manager.update_task_capabilities(task_id, body.required_capabilities)

    broadcast_task_updated(request.app)
    task = await task_manager.get_task(task_id)
    return JSONResponse({"id": str(task_id), "title": task.title if task else None})


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


class MergeRequest(BaseModel):
    skip_merge: bool = False


@router.post("/tasks/{task_id}/merge")
async def merge_task(
    task_id: UUID, request: Request, body: MergeRequest = MergeRequest()
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
            detail=f"Task {task_id} is not ready for merge",
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

    from core.qa_runner import load_deployment_config

    ratchet_yaml = project.ratchet_yaml if project.config_source == "db" else None
    deployment_config = load_deployment_config(str(project.local_path), ratchet_yaml)

    if deployment_config.mode == "pr":
        local_path = str(project.local_path)
        base_branch = deployment_config.base_branch
        try:
            subprocess.run(
                ["git", "push", "origin", branch_name],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
            pr_title = f"feat: {task.title} (task/{task_id})"
            pr_result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--base", base_branch,
                    "--title", pr_title,
                    "--body", "",
                ],
                cwd=local_path,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or f"command failed with exit code {exc.returncode}"
            raise HTTPException(status_code=400, detail=detail)

        pr_url = pr_result.stdout.strip().splitlines()[-1].strip()
        pr_number = int(pr_url.rstrip("/").split("/")[-1])

        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_PR_CREATED,
            payload={"pr_url": pr_url, "pr_number": pr_number, "branch": branch_name},
        )

        task = await task_manager.get_task(task_id)
        return JSONResponse({"task": task.model_dump(mode="json") if task else None})

    if not body.skip_merge:
        import uuid as _uuid_mod

        local_path = str(project.local_path)
        title = task.title
        target_branch = "develop"

        # Use a temporary worktree so the main working directory is never switched.
        merge_worktree = os.path.join(
            local_path, ".worktrees", f"deploy-{_uuid_mod.uuid4()}"
        )
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", merge_worktree, target_branch],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode().strip()
            raise HTTPException(status_code=400, detail=stderr or "git worktree add failed")

        try:
            try:
                subprocess.run(
                    ["git", "merge", "--squash", branch_name],
                    cwd=merge_worktree,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as merge_exc:
                merge_output = (merge_exc.stderr or b"").decode()

                conflict_result = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=U"],
                    cwd=merge_worktree,
                    capture_output=True,
                    text=True,
                )
                conflicted_files = [
                    f for f in conflict_result.stdout.strip().splitlines() if f
                ]

                from uuid import uuid4 as _uuid4

                from core.context_assembler import (
                    ExecutionContext,
                    build_conflict_resolution_prompt,
                    read_intent,
                )
                from core.invoker import ClaudeCodeInvoker

                spec_id: UUID | None = None
                spec_content = ""
                task_events = await store.get_events(task_id, "task")
                for tevt in reversed(task_events):
                    if tevt.event_type == ev.TASK_SPEC_ASSIGNED:
                        sid = tevt.payload.get("spec_id")
                        if sid:
                            spec_id = UUID(sid)
                        break
                if spec_id is not None:
                    spec_events = await store.get_events(spec_id, "spec")
                    for sevt in spec_events:
                        if sevt.event_type == ev.SPEC_CREATED:
                            spec_content = sevt.payload.get("content", "")
                            break

                intent_content = read_intent(local_path)
                prompt = build_conflict_resolution_prompt(
                    intent_content=intent_content,
                    spec_content=spec_content,
                    conflicted_files=conflicted_files,
                    merge_output=merge_output,
                )

                resolution_execution_id = _uuid4()
                context = ExecutionContext(
                    execution_id=resolution_execution_id,
                    task_id=task_id,
                    spec_id=spec_id or task_id,
                    worktree_path=merge_worktree,
                    prompt=prompt,
                )
                result = ClaudeCodeInvoker(store=store).invoke(context)

                if result.status != "completed":
                    subprocess.run(
                        ["git", "merge", "--abort"],
                        cwd=merge_worktree,
                        capture_output=True,
                    )
                    detail = (
                        f"Merge conflict: {merge_output}\n"
                        f"Conflict resolution failed: {result.failure_reason}"
                    )
                    raise HTTPException(status_code=409, detail=detail)

            has_staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=merge_worktree,
                capture_output=True,
            ).returncode != 0
            if has_staged:
                commit_msg = f"feat: {title} (task/{task_id})"
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=merge_worktree,
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
        except HTTPException:
            raise
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode().strip()
            stdout = (exc.stdout or b"").decode().strip()
            detail = stderr or stdout or f"git command failed with exit code {exc.returncode}"
            raise HTTPException(status_code=400, detail=detail)
        finally:
            new_sha_proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=merge_worktree,
                capture_output=True,
                text=True,
            )
            new_sha = new_sha_proc.stdout.strip()
            subprocess.run(
                ["git", "worktree", "remove", "--force", merge_worktree],
                cwd=local_path,
                capture_output=True,
            )

        # Advance the target branch to the new commit and sync local_path.
        if new_sha:
            subprocess.run(
                ["git", "update-ref", f"refs/heads/{target_branch}", new_sha],
                cwd=local_path,
                capture_output=True,
            )
        subprocess.run(
            ["git", "checkout", target_branch],
            cwd=local_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", target_branch],
            cwd=local_path,
            capture_output=True,
        )

    if not body.skip_merge:
        from core.qa_runner import load_merge_config, run_merge_steps

        deploy_config = load_merge_config(str(project.local_path))
        if deploy_config and deploy_config.steps:
            hook_results = await asyncio.get_event_loop().run_in_executor(
                None, run_merge_steps, deploy_config, str(project.local_path)
            )
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPLOY_HOOKS_RUN,
                payload={
                    "steps": [
                        {
                            "name": r.step_name,
                            "command": r.command,
                            "returncode": r.returncode,
                            "output": r.output,
                        }
                        for r in hook_results
                    ]
                },
            )

    await state_machine.transition(task_id, ev.DEPLOYED)

    task = await task_manager.get_task(task_id)
    return JSONResponse({"task": task.model_dump(mode="json") if task else None})


class ArchiveTaskBody(BaseModel):
    reason: str | None = None


@router.post("/tasks/{task_id}/archive")
async def archive_task(
    task_id: UUID, request: Request, body: ArchiveTaskBody = ArchiveTaskBody()
) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    task_manager = TaskManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    extra_payload = {"reason": body.reason} if body.reason else None
    try:
        await state_machine.transition(task_id, ev.ABANDONED, extra_payload=extra_payload)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
produce the spec following the guidelines and format below.

## Spec Writing Guidelines

Apply these principles when writing the spec:

### Specificity Beats Abstraction
Agents struggle with "improve performance" but excel with "reduce database queries in \
UserService.findAll() by adding eager loading for relationships". Abstract goals require \
human judgment; concrete goals enable autonomous execution.

### Examples Are Executable Documentation
A single example of desired output is worth 1000 words of description. "Follow the NBA \
pattern" only works if you point to specific files and explain what to copy.

### Constraints Prevent Waste
Without explicit boundaries, agents over-engineer solutions. "Out of scope" sections save \
more time than "in scope" sections.

### Verification Criteria Enable Autonomy
Testable success criteria let agents validate their own work. "Links should have proper \
values" fails; "All links must include cid=2026olympicsoli" succeeds.

### Vertical Slices, Not Horizontal Layers
Tasks must be thin vertical slices — each cutting through all relevant layers of the \
system — not horizontal single-layer tasks. Each slice should be independently verifiable \
and produce a working increment.

BAD (horizontal layers):
- Add all database schema changes
- Add all API endpoints
- Write all tests

GOOD (vertical slices):
- Basic user creation: schema + endpoint + test
- User validation: constraints + error responses + test
- User search: query + endpoint + test

Start with the thinnest possible end-to-end path ("tracer bullet"). Each subsequent slice \
adds one capability. Each slice includes its own tests.

### Anti-Patterns to Avoid
- Vague objectives: "Improve handling" → "Set static CID to 'X' for messages where \
league equals 'Y'"
- Missing examples: "Follow the existing pattern" → "Follow NBAUrlStrategy in \
url-strategies.ts lines 48-111"
- Unbounded scope: No "Out of Scope" section → explicit list of what NOT to change
- Untestable success: "Works correctly" → specific function returns specific values
- Implicit knowledge: "Update the strategy" → "Create class in file X, copy from \
lines Y-Z, replace A with B"

## Spec Format

Produce the spec in this exact format:

## SPEC READY
# Spec N: <title>

## Objective
[One concrete sentence. Specific enough that an agent can determine when it's complete. \
Use measurable outcomes.]

## Success Criteria
- [ ] [Specific, verifiable outcome with exact values]
- [ ] [Another verifiable outcome — function signatures, field names, etc.]
- [ ] [Test command succeeds]
- [ ] [Lint/typecheck passes]

## Out of Scope
- [Explicit boundary — what NOT to change or create]

## Technical Context
- Stack: [Language, framework, etc.]
- Entry point: [Path to main file]
- Pattern to follow: [File path with line numbers]
- Related files:
  - [File path — purpose]

## Data Examples
**Input:** [sample]
**Expected Output:** [sample]

## Tasks (vertical slices — each independently testable)
- [ ] [Slice 1: thinnest end-to-end path + its own test]
- [ ] [Slice 2: next capability + test]
- [ ] [Run full validation commands]

## Test Requirements
- Framework: [pytest, jest, etc.]
- Scenarios:
  - [Happy path]
  - [Edge case]
  - [Error condition]

## Assumptions
- [What the agent can assume is true]

## Verification Commands
```bash
...
```

## What Exists After This Spec
...

Be specific about file paths, function names, and test requirements. Read the codebase \
to understand current patterns before generating the spec.

IMPORTANT: Every response must contain visible text. If you are reading files or using
tools, first write a brief message like "Let me read the codebase before writing the spec."
Never produce a response that consists only of tool use with no text.

## User's Opening Description

{user_description}"""


def _build_initial_spec_prompt(intent_md: str, task_title: str, user_description: str) -> str:
    return _SPEC_ROLE_PROMPT.format(
        intent_md=intent_md,
        task_title=task_title,
        user_description=user_description,
    )


