"""WebSocket endpoint for worker connections — /ws/worker."""

from __future__ import annotations

import asyncio
import logging
import os
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core import events as ev
from core import git_transfer
from core.execution_manager import ExecutionManager
from core.git_transfer import GitTransferError
from core.project_manager import ProjectManager
from core.remote_protocol import (
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
    GetStatusRequest,
    GetStatusResponse,
    HeartbeatMessage,
    LogLineMessage,
    OrchestratorAckMessage,
    QuestionAskedMessage,
    WorkerHelloMessage,
    parse_worker_message,
)
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager
from orchestrator.channel import WebSocketWorkerChannel
from orchestrator.registry import WorkerConnection, WorkerRegistry

logger = logging.getLogger(__name__)

# Maps execution_id → pending delayed-cleanup asyncio.Task
_pending_disconnects: dict[str, asyncio.Task[None]] = {}

router = APIRouter()


async def _handle_execution_completed(
    store: Store, registry: WorkerRegistry, worker_id: str, msg: ExecutionCompletedMessage
) -> None:
    """Apply patch, record completion, transition task to ready_for_qa, clear job."""
    try:
        execution_id = UUID(msg.execution_id)
        task_id = UUID(msg.task_id)
    except ValueError:
        logger.error(
            "_handle_execution_completed: invalid UUIDs execution_id=%s task_id=%s, clearing job",
            msg.execution_id,
            msg.task_id,
        )
        registry.clear_job(worker_id)
        return

    task = await TaskManager(store).get_task(task_id)
    if task is None:
        logger.error(
            "_handle_execution_completed: task %s not found, clearing job", task_id
        )
        registry.clear_job(worker_id)
        return

    project = await ProjectManager(store).get_project(task.project_id)
    if project is None:
        logger.error(
            "_handle_execution_completed: project %s not found, clearing job", task.project_id
        )
        registry.clear_job(worker_id)
        return

    worktree_path = os.path.join(project.local_path, ".worktrees", str(execution_id))
    execution_manager = ExecutionManager(store, project.local_path)
    state_machine = TaskStateMachine(store)

    try:
        git_transfer.apply_patch(worktree_path, msg.patch)
    except GitTransferError as err:
        reason = f"patch apply failed: {err}"
        logger.warning(
            "_handle_execution_completed: patch failed for execution %s: %s", execution_id, reason
        )
        await execution_manager.fail_execution(execution_id, reason)
        await state_machine.transition(task_id, ev.BLOCKED)
        registry.clear_job(worker_id)
        return

    await execution_manager.complete_execution(execution_id)
    await state_machine.transition(task_id, ev.READY_FOR_QA)
    registry.clear_job(worker_id)
    logger.info(
        "_handle_execution_completed: execution %s completed, task %s -> ready_for_qa",
        execution_id,
        task_id,
    )


async def _handle_execution_failed(
    store: Store, registry: WorkerRegistry, worker_id: str, msg: ExecutionFailedMessage
) -> None:
    """Record failure, transition task to blocked, clear job."""
    try:
        execution_id = UUID(msg.execution_id)
        task_id = UUID(msg.task_id)
    except ValueError:
        logger.error(
            "_handle_execution_failed: invalid UUIDs execution_id=%s task_id=%s, clearing job",
            msg.execution_id,
            msg.task_id,
        )
        registry.clear_job(worker_id)
        return

    task = await TaskManager(store).get_task(task_id)
    if task is None:
        logger.error(
            "_handle_execution_failed: task %s not found, clearing job", task_id
        )
        registry.clear_job(worker_id)
        return

    project = await ProjectManager(store).get_project(task.project_id)
    if project is None:
        logger.error(
            "_handle_execution_failed: project %s not found, clearing job", task.project_id
        )
        registry.clear_job(worker_id)
        return

    await ExecutionManager(store, project.local_path).fail_execution(
        execution_id, msg.failure_reason
    )
    await TaskStateMachine(store).transition(task_id, ev.BLOCKED)
    registry.clear_job(worker_id)
    logger.info(
        "_handle_execution_failed: execution %s failed, task %s -> blocked",
        execution_id,
        task_id,
    )


async def _do_disconnect_cleanup(
    worker_conn: WorkerConnection, store: Store, registry: WorkerRegistry
) -> None:
    """Execute disconnect cleanup: fail the execution, return task to ready_for_implementation."""
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


async def _find_worker_with_execution(
    registry: WorkerRegistry, execution_id: str
) -> bool:
    """Return True if any connected worker reports currently executing execution_id."""
    for conn in registry.all_workers():
        channel = WebSocketWorkerChannel(conn.websocket, conn.worker_id)
        try:
            resp = await channel.send_command(
                GetStatusRequest(type="get_status", request_id=str(uuid4()))
            )
            if (
                isinstance(resp, GetStatusResponse)
                and resp.current_execution_id == execution_id
            ):
                return True
        except Exception:
            pass
    return False


async def _delayed_disconnect_cleanup(
    worker_conn: WorkerConnection,
    store: Store,
    registry: WorkerRegistry,
    timeout: float,
) -> None:
    """Wait for reconnect window then reset task if no worker resumed."""
    execution_id = worker_conn.current_execution_id
    assert execution_id is not None

    try:
        await asyncio.sleep(timeout)

        # Check if any connected worker resumed this execution
        if await _find_worker_with_execution(registry, execution_id):
            logger.info(
                "handle_disconnect: worker resumed execution %s, cancelling cleanup",
                execution_id,
            )
            return

        logger.info(
            "handle_disconnect: no worker resumed execution %s after %.0fs, resetting task",
            execution_id,
            timeout,
        )
        await _do_disconnect_cleanup(worker_conn, store, registry)
    except asyncio.CancelledError:
        logger.info(
            "handle_disconnect: pending cleanup for execution %s was cancelled",
            execution_id,
        )
    finally:
        _pending_disconnects.pop(execution_id, None)


async def handle_disconnect(
    worker_conn: WorkerConnection, store: Store, registry: WorkerRegistry
) -> None:
    """Handle worker disconnect with configurable grace period before resetting task.

    Schedules a delayed cleanup task. If the worker reconnects and resumes the
    execution within WORKER_RECONNECT_TIMEOUT_SECONDS, the cleanup is skipped.
    """
    if worker_conn.current_execution_id is None:
        return

    execution_id = worker_conn.current_execution_id
    timeout = float(os.environ.get("WORKER_RECONNECT_TIMEOUT_SECONDS", "30"))

    logger.info(
        "handle_disconnect: worker %s disconnected with execution %s,"
        " scheduling cleanup in %.0fs",
        worker_conn.worker_id,
        execution_id,
        timeout,
    )

    cleanup_task = asyncio.create_task(
        _delayed_disconnect_cleanup(worker_conn, store, registry, timeout)
    )
    _pending_disconnects[execution_id] = cleanup_task


@router.websocket("/ws/worker")
async def ws_worker(websocket: WebSocket) -> None:
    await websocket.accept()
    worker_id = str(uuid4())
    registry: WorkerRegistry = websocket.app.state.registry
    store: Store = websocket.app.state.store

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
    logger.info(
        "Worker %s registered with capabilities: %s",
        worker_id,
        incoming.capabilities or "(none)",
    )
    ack = OrchestratorAckMessage(
        type="orchestrator_ack", worker_id=worker_id, accepted=True, message="registered"
    )
    await websocket.send_text(ack.model_dump_json())

    # Create the channel BEFORE the main loop starts. The GetStatus check below
    # uses direct websocket recv (no main loop yet), but subsequent commands from
    # the sequencer go through the queue-based channel.
    channel = WebSocketWorkerChannel(websocket, worker_id)
    conn_entry = registry.get_worker(worker_id)
    if conn_entry is not None:
        conn_entry.channel = channel

    # Check if this worker is resuming an execution whose cleanup is pending.
    # This runs before the main loop, so direct recv is safe here.
    if _pending_disconnects:
        try:
            status_req = GetStatusRequest(type="get_status", request_id=str(uuid4()))
            await websocket.send_text(status_req.model_dump_json())
            raw_status = await websocket.receive_text()
            status_resp = parse_worker_message(raw_status)
            if (
                isinstance(status_resp, GetStatusResponse)
                and status_resp.current_execution_id
                and status_resp.current_execution_id in _pending_disconnects
            ):
                pending_task = _pending_disconnects.pop(status_resp.current_execution_id)
                pending_task.cancel()
                logger.info(
                    "ws_worker: reconnected worker %s cancelled pending cleanup"
                    " for execution %s",
                    worker_id,
                    status_resp.current_execution_id,
                )
        except Exception:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            msg = parse_worker_message(raw)

            # Command responses have a request_id field — route to the channel
            if hasattr(msg, "request_id"):
                await channel.deliver_response(raw)
            elif isinstance(msg, ExecutionStartedMessage):
                logger.debug(
                    "execution started: worker=%s execution=%s", worker_id, msg.execution_id
                )
            elif isinstance(msg, ExecutionCompletedMessage):
                await _handle_execution_completed(store, registry, worker_id, msg)
            elif isinstance(msg, ExecutionFailedMessage):
                await _handle_execution_failed(store, registry, worker_id, msg)
            elif isinstance(msg, HeartbeatMessage | LogLineMessage | QuestionAskedMessage):
                logger.debug("received %s from worker %s", type(msg).__name__, worker_id)
            else:
                logger.warning("unknown message type from worker %s: %s", worker_id, type(msg))
    except WebSocketDisconnect:
        logger.info("worker %s disconnected", worker_id)
    finally:
        conn = registry.unregister(worker_id)
        if conn is not None:
            await handle_disconnect(conn, store, registry)
