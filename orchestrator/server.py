"""FastAPI orchestrator server — health, worker status, and WebSocket worker endpoint."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

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
from orchestrator.dispatcher import JobDispatcher
from orchestrator.registry import WorkerRegistry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        raise RuntimeError("DATABASE_URL environment variable is required")

    registry = WorkerRegistry()
    dispatcher = JobDispatcher(registry)
    dispatch_task = asyncio.create_task(dispatcher.dispatch_loop())

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
        registry.unregister(worker_id)
