"""Tasks routes: GET/POST /tasks/{task_id}, spec assignment, deploy."""

from __future__ import annotations

import subprocess
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from core import events as ev
from core.state_machine import TaskStateMachine
from core.store import PostgresStore
from web import queries
from web.sse import broadcast_task_updated
from web.templating import templates

router = APIRouter()


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: UUID, request: Request) -> Response:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        task = await queries.get_task(conn, task_id)
        if task is None:
            return templates.TemplateResponse(
                "404.html",
                {"request": request, "message": f"Task {task_id} not found"},
                status_code=404,
            )
        project = await queries.get_project(conn, UUID(str(task["project_id"])))
        specs = await queries.get_task_specs(conn, task_id)
        executions = await queries.get_task_executions(conn, task_id)
        deps = await queries.get_task_dependencies(conn, task_id)
        qa_failure_reason = await queries.get_task_qa_failure_reason(conn, task_id)

    current_spec_id = task.get("current_spec_id")
    current_spec = None
    if current_spec_id is not None:
        for s in specs:
            if str(s["id"]) == str(current_spec_id):
                current_spec = s
                break

    return templates.TemplateResponse(
        "tasks/detail.html",
        {
            "request": request,
            "task": task,
            "project": project,
            "specs": specs,
            "executions": executions,
            "deps": deps,
            "current_spec": current_spec,
            "qa_failure_reason": qa_failure_reason,
        },
    )


@router.get("/tasks/{task_id}/spec/new", response_class=HTMLResponse)
async def spec_new_form(task_id: UUID, request: Request) -> Response:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        task = await queries.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return templates.TemplateResponse(
        "tasks/spec_new.html",
        {"request": request, "task": task, "error": None},
    )


@router.post("/tasks/{task_id}/spec")
async def assign_spec(
    request: Request,
    task_id: UUID,
    content: Annotated[str, Form()],
) -> Response:
    from core import events as ev
    from core.spec_manager import SpecManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    pool = request.app.state.pool
    store = PostgresStore(pool)
    state_machine = TaskStateMachine(store)
    spec_manager = SpecManager(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    allowed = {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION}
    if current_status not in allowed:
        async with pool.connection() as conn:
            task = await queries.get_task(conn, task_id)
        return templates.TemplateResponse(
            "tasks/spec_new.html",
            {
                "request": request,
                "task": task,
                "error": f"Cannot assign spec to task in status {current_status!r}",
            },
            status_code=400,
        )

    current_spec = await spec_manager.get_current_spec(task_id)
    previous_spec_id = current_spec.id if current_spec is not None else None

    spec = await spec_manager.create_spec(task_id, content, previous_spec_id)
    await spec_manager.assign_spec(task_id, spec.id)

    try:
        if current_status == ev.READY_FOR_SPEC:
            await state_machine.transition(task_id, ev.SPEC_QA)
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        elif current_status in (ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION):
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    except InvalidTransitionError as exc:
        async with pool.connection() as conn:
            task = await queries.get_task(conn, task_id)
        return templates.TemplateResponse(
            "tasks/spec_new.html",
            {
                "request": request,
                "task": task,
                "error": str(exc),
            },
            status_code=400,
        )

    broadcast_task_updated(request.app)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@router.get("/tasks/{task_id}/deploy", response_class=HTMLResponse)
async def deploy_confirm(task_id: UUID, request: Request) -> Response:
    pool = request.app.state.pool
    store = PostgresStore(pool)
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status != ev.READY_FOR_DEPLOYMENT:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not ready for deployment")

    async with pool.connection() as conn:
        task = await queries.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    execution_events = await store.get_events(task_id, "task_executions")
    branch_name: str | None = None
    for event in reversed(execution_events):
        if event.event_type == ev.EXECUTION_STARTED:
            bn = event.payload.get("branch_name")
            if bn:
                branch_name = bn
            break

    return templates.TemplateResponse(
        "tasks/deploy_confirm.html",
        {
            "request": request,
            "task": task,
            "branch_name": branch_name,
            "target_branch": "develop",
        },
    )


@router.post("/tasks/{task_id}/deploy")
async def deploy_task(
    request: Request,
    task_id: UUID,
    target_branch: Annotated[str, Form()] = "develop",
    skip_merge: Annotated[str | None, Form()] = None,
) -> Response:
    pool = request.app.state.pool
    store = PostgresStore(pool)
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status != ev.READY_FOR_DEPLOYMENT:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not ready for deployment")

    async with pool.connection() as conn:
        task = await queries.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        project_id = UUID(str(task["project_id"]))
        project = await queries.get_project(conn, project_id)

    if project is None:
        raise HTTPException(status_code=404, detail=f"Project for task {task_id} not found")

    if not skip_merge:
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

        local_path = str(project["local_path"])
        title = str(task["title"])

        try:
            subprocess.run(
                ["git", "checkout", target_branch],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            return templates.TemplateResponse(
                "400.html",
                {"request": request, "detail": exc.stderr.decode()},
                status_code=400,
            )

        try:
            subprocess.run(
                ["git", "merge", "--squash", branch_name],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            return templates.TemplateResponse(
                "400.html",
                {"request": request, "detail": exc.stderr.decode()},
                status_code=400,
            )

        commit_msg = f"feat: {title} (task/{task_id})"
        try:
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            return templates.TemplateResponse(
                "400.html",
                {"request": request, "detail": exc.stderr.decode()},
                status_code=400,
            )

        try:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # non-fatal

    await state_machine.transition(task_id, ev.DEPLOYED)
    broadcast_task_updated(request.app)
    request.session["flash"] = f"Deployed: {task['title']}"
    return RedirectResponse(url="/", status_code=303)


@router.post("/tasks/{task_id}/reset")
async def reset_task(request: Request, task_id: UUID) -> Response:
    pool = request.app.state.pool
    store = PostgresStore(pool)
    state_machine = TaskStateMachine(store)

    current_status = await state_machine.get_current_status(task_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if current_status != ev.BLOCKED:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not blocked")

    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    broadcast_task_updated(request.app)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)
