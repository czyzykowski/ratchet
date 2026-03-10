"""Tasks routes: GET/POST /tasks/{task_id}, spec assignment."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from web import queries
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

    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)
