"""FastAPI orchestrator server — health, worker status, and WebSocket worker endpoint."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from core import events as ev
from core.execution_manager import ExecutionManager
from core.project_manager import ProjectManager
from core.remote_protocol import (
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
    HeartbeatMessage,
    LogLineMessage,
    OrchestratorAckMessage,
    QuestionAskedMessage,
    WorkerHelloMessage,
    parse_worker_message,
)
from core.state_machine import TaskStateMachine
from core.store import PostgresStore, Store
from core.task_manager import TaskManager
from orchestrator.dispatcher import JobDispatcher
from orchestrator.registry import WorkerConnection, WorkerRegistry

logger = logging.getLogger(__name__)


async def handle_disconnect(
    worker_conn: WorkerConnection, store: Store, registry: WorkerRegistry
) -> None:
    """Handle worker disconnect: fail active execution and return task to ready_for_implementation.
    """
    if worker_conn.current_execution_id is None:
        return

    try:
        execution_id = UUID(worker_conn.current_execution_id)

        # Resolve task_id from execution events
        execution_events = await store.get_events(execution_id, "execution")
        task_id: UUID | None = None
        for event in execution_events:
            if event.event_type == ev.EXECUTION_STARTED:
                task_id = UUID(event.payload["task_id"])
                break

        if task_id is None:
            logger.warning(
                "handle_disconnect: no EXECUTION_STARTED event found for execution %s",
                execution_id,
            )
            return

        # Append audit event
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_WORKER_DISCONNECTED,
            payload={
                "execution_id": str(execution_id),
                "worker_id": worker_conn.worker_id,
            },
        )

        # Resolve repo_path
        task = await TaskManager(store).get_task(task_id)
        repo_path = ""
        if task is None:
            logger.warning(
                "handle_disconnect: task %s not found, proceeding with empty repo_path",
                task_id,
            )
        else:
            project = await ProjectManager(store).get_project(task.project_id)
            if project is None:
                logger.warning(
                    "handle_disconnect: project %s not found, proceeding with empty repo_path",
                    task.project_id,
                )
            else:
                repo_path = project.local_path

        # Fail the execution
        em = ExecutionManager(store, repo_path)
        await em.fail_execution(execution_id, "worker disconnected")

        # Return task to ready_for_implementation
        sm = TaskStateMachine(store)
        await sm.transition(
            task_id,
            ev.READY_FOR_IMPLEMENTATION,
            extra_payload={"reason": "worker_disconnected"},
        )
    except Exception:
        logger.error(
            "handle_disconnect: unexpected error for worker %s",
            worker_conn.worker_id,
            exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        raise RuntimeError("DATABASE_URL environment variable is required")

    store = PostgresStore()
    registry = WorkerRegistry()
    dispatcher = JobDispatcher(registry)
    dispatch_task = asyncio.create_task(dispatcher.dispatch_loop())

    app.state.store = store
    app.state.registry = registry
    app.state.dispatcher = dispatcher
    app.state.dispatch_task = dispatch_task

    yield

    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    workers = len(app.state.registry.all_workers())
    return {"status": "ok", "workers": workers}


@app.get("/workers")
async def workers() -> list[dict[str, object]]:
    return [
        {
            "id": w.worker_id,
            "capabilities": w.capabilities,
            "current_execution_id": w.current_execution_id,
        }
        for w in app.state.registry.all_workers()
    ]


@app.websocket("/ws/worker")
async def ws_worker(websocket: WebSocket) -> None:
    await websocket.accept()
    worker_id = str(uuid4())
    registry: WorkerRegistry = app.state.registry
    dispatcher: JobDispatcher = app.state.dispatcher

    raw = await websocket.receive_text()
    incoming = parse_worker_message(raw)

    if not isinstance(incoming, WorkerHelloMessage):
        ack = OrchestratorAckMessage(
            type="orchestrator_ack",
            worker_id=worker_id,
            accepted=False,
            message="expected WorkerHelloMessage",
        )
        await websocket.send_text(ack.model_dump_json())
        await websocket.close()
        return

    registry.register(worker_id, incoming.capabilities, websocket)
    ack = OrchestratorAckMessage(
        type="orchestrator_ack", worker_id=worker_id, accepted=True, message="registered"
    )
    await websocket.send_text(ack.model_dump_json())

    try:
        while True:
            raw = await websocket.receive_text()
            msg = parse_worker_message(raw)

            if isinstance(msg, ExecutionStartedMessage):
                dispatcher.handle_execution_started(worker_id, msg)
            elif isinstance(msg, ExecutionCompletedMessage):
                dispatcher.handle_execution_completed(worker_id, msg)
                registry.clear_job(worker_id)
            elif isinstance(msg, ExecutionFailedMessage):
                dispatcher.handle_execution_failed(worker_id, msg)
                registry.clear_job(worker_id)
            elif isinstance(msg, HeartbeatMessage | LogLineMessage | QuestionAskedMessage):
                logger.debug("received %s from worker %s", type(msg).__name__, worker_id)
            else:
                logger.warning("unknown message type from worker %s: %s", worker_id, type(msg))
    except WebSocketDisconnect:
        logger.info("worker %s disconnected", worker_id)
    finally:
        conn = registry.unregister(worker_id)
        if conn is not None:
            store: Store = app.state.store
            await handle_disconnect(conn, store, registry)
