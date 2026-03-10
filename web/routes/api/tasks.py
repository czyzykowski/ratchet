"""API: task endpoints under /api/tasks."""

from __future__ import annotations

import subprocess
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import events as ev
from core.execution_manager import ExecutionManager
from core.models import Event
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import InvalidTransitionError, TaskStateMachine

router = APIRouter()


def _build_task_dict(task_id: UUID, task_events: list[Event]) -> dict[str, Any] | None:
    """Replay task events to build a task dict. Returns None if no TASK_CREATED event found."""
    task: dict[str, Any] | None = None
    depends_on: list[str] = []
    current_spec_id: str | None = None
    refinement_count = 0

    for event in task_events:
        if event.event_type == ev.TASK_CREATED:
            p = event.payload
            task = {
                "id": str(task_id),
                "project_id": str(p["project_id"]),
                "title": p.get("title", ""),
                "status": p.get("status", ev.READY_FOR_SPEC),
                "current_spec_id": None,
                "refinement_count": 0,
                "depends_on": [],
                "created_at": event.occurred_at.isoformat(),
                "updated_at": event.occurred_at.isoformat(),
            }
        elif event.event_type == ev.TASK_STATUS_CHANGED and task is not None:
            task["status"] = event.payload["to_status"]
            task["updated_at"] = event.occurred_at.isoformat()
        elif event.event_type == ev.TASK_SPEC_ASSIGNED:
            current_spec_id = event.payload.get("spec_id")
            refinement_count += 1
        elif event.event_type == ev.TASK_DEPENDENCY_ADDED:
            depends_on.extend(event.payload.get("depends_on", []))
        elif event.event_type == ev.TASK_TITLE_UPDATED and task is not None:
            task["title"] = event.payload["title"]
            task["updated_at"] = event.occurred_at.isoformat()

    if task is not None:
        task["depends_on"] = depends_on
        task["current_spec_id"] = current_spec_id
        task["refinement_count"] = refinement_count

    return task


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

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
    qa_failure: str | None = None
    for event in reversed(task_events):
        if (
            event.event_type == ev.TASK_STATUS_CHANGED
            and event.payload.get("to_status") == ev.BLOCKED
        ):
            qa_failure = event.payload.get("failure_reason")
            break

    return JSONResponse(
        {
            "task": task,
            "specs": specs_data,
            "executions": executions_data,
            "dependencies": task.get("depends_on", []),
            "qa_failure": qa_failure,
        }
    )


class CreateTaskBody(BaseModel):
    project_id: str
    title: str


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    task_id = uuid4()
    project_id = UUID(body.project_id)

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": body.title,
            "status": ev.READY_FOR_SPEC,
        },
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": body.title,
        },
    )

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    return JSONResponse({"task": task}, status_code=201)


class UpdateTaskBody(BaseModel):
    title: str


@router.patch("/tasks/{task_id}")
async def update_task_title(task_id: UUID, body: UpdateTaskBody, request: Request) -> JSONResponse:
    store = request.app.state.store

    task_events = await store.get_events(task_id, "task")
    if not task_events:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_TITLE_UPDATED,
        payload={"title": body.title},
    )

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    return JSONResponse({"task": task})


class AssignSpecBody(BaseModel):
    content: str


@router.post("/tasks/{task_id}/spec")
async def assign_spec(task_id: UUID, body: AssignSpecBody, request: Request) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    spec_manager = SpecManager(store)

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

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    spec_data = {
        "id": str(spec.id),
        "task_id": str(spec.task_id),
        "previous_spec_id": str(spec.previous_spec_id) if spec.previous_spec_id else None,
        "content": spec.content,
        "created_at": spec.created_at.isoformat(),
    }
    return JSONResponse({"task": task, "spec": spec_data})


@router.post("/tasks/{task_id}/reset")
async def reset_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.BLOCKED:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not blocked")

    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    return JSONResponse({"task": task})


@router.post("/tasks/{task_id}/deploy")
async def deploy_task(task_id: UUID, request: Request) -> JSONResponse:
    store = request.app.state.store
    state_machine = TaskStateMachine(store)
    pm = ProjectManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.READY_FOR_DEPLOYMENT:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not ready for deployment",
        )

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    project_id = UUID(task["project_id"])
    project = await pm.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

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

    local_path = str(project.local_path)
    title = task["title"]
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
        raise HTTPException(status_code=400, detail=exc.stderr.decode())

    await state_machine.transition(task_id, ev.DEPLOYED)

    task_events = await store.get_events(task_id, "task")
    task = _build_task_dict(task_id, task_events)
    return JSONResponse({"task": task})
