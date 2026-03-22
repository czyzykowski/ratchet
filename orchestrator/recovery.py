"""Orchestrator crash recovery: identify and resolve in-progress tasks on startup."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from core import events as ev
from core.remote_protocol import GetStatusRequest, GetStatusResponse
from core.state_machine import TaskStateMachine
from core.store import Store
from orchestrator.channel import WebSocketWorkerChannel
from orchestrator.registry import WorkerRegistry
from web.queries import get_in_progress_task_ids

logger = logging.getLogger(__name__)


@dataclass
class RecoveryAction:
    """Represents an in-progress task that needs recovery after orchestrator restart."""

    task_id: UUID
    execution_id: UUID
    worker_id: str


class RecoveryManager:
    """Identifies orphaned in-progress tasks on startup and resolves them.

    On startup, queries the materialized view for in-progress tasks, replays
    their events to find the assigned worker/execution, waits for a grace period
    to allow workers to reconnect, then either resumes monitoring or resets
    orphaned tasks to ready_for_implementation.
    """

    def __init__(
        self,
        store: Store,
        registry: WorkerRegistry,
        grace_period: float = 60.0,
    ) -> None:
        self._store = store
        self._registry = registry
        self._grace_period = grace_period

    async def recover_in_progress_tasks(self, pool: Any) -> list[RecoveryAction]:
        """Find in-progress tasks via materialized view and build recovery actions.

        For each in-progress task, replays events to find the latest
        TASK_ASSIGNED_TO_WORKER event. Skips tasks whose execution is already
        terminal (EXECUTION_COMPLETED or EXECUTION_FAILED).
        """
        task_ids = await get_in_progress_task_ids(pool)
        actions: list[RecoveryAction] = []

        for task_id in task_ids:
            task_events = await self._store.get_events(task_id, "task")

            worker_id: str | None = None
            execution_id_str: str | None = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_ASSIGNED_TO_WORKER:
                    worker_id = event.payload.get("worker_id")
                    execution_id_str = event.payload.get("execution_id")
                    break

            if not worker_id or not execution_id_str or execution_id_str == "pending":
                logger.warning(
                    "recovery: task %s is in_progress with no valid assignment event,"
                    " resetting directly",
                    task_id,
                )
                await self._reset_orphaned_task(task_id, None)
                continue

            try:
                execution_id = UUID(execution_id_str)
            except ValueError:
                logger.warning(
                    "recovery: task %s has invalid execution_id %r, skipping",
                    task_id,
                    execution_id_str,
                )
                continue

            # Check if execution is already terminal
            exec_events = await self._store.get_events(execution_id, "execution")
            is_terminal = any(
                e.event_type in (ev.EXECUTION_COMPLETED, ev.EXECUTION_FAILED)
                for e in exec_events
            )
            if is_terminal:
                logger.info(
                    "recovery: task %s execution %s is already terminal, skipping",
                    task_id,
                    execution_id,
                )
                continue

            logger.info(
                "recovery: task %s has in-flight execution %s assigned to worker %s",
                task_id,
                execution_id,
                worker_id,
            )
            actions.append(
                RecoveryAction(
                    task_id=task_id,
                    execution_id=execution_id,
                    worker_id=worker_id,
                )
            )

        return actions

    async def wait_and_resolve(self, actions: list[RecoveryAction]) -> None:
        """Wait grace period then resolve each action.

        For each action, checks if a connected worker is executing the same
        execution_id (via GetStatusRequest). If so, logs 'resumed' and leaves
        the task in-progress. Otherwise resets it to ready_for_implementation.
        """
        logger.info(
            "recovery: waiting %.0fs grace period for %d in-flight task(s) to reconnect",
            self._grace_period,
            len(actions),
        )
        await asyncio.sleep(self._grace_period)

        for action in actions:
            await self._resolve_action(action)

    async def _resolve_action(self, action: RecoveryAction) -> None:
        """Check if a worker resumed the execution; if not, reset the task."""
        for worker_conn in self._registry.all_workers():
            channel = WebSocketWorkerChannel(worker_conn.websocket, worker_conn.worker_id)
            try:
                resp = await channel.send_command(
                    GetStatusRequest(type="get_status", request_id=str(uuid4()))
                )
                if (
                    isinstance(resp, GetStatusResponse)
                    and resp.current_execution_id == str(action.execution_id)
                ):
                    logger.info(
                        "recovery: worker %s resumed execution %s for task %s",
                        worker_conn.worker_id,
                        action.execution_id,
                        action.task_id,
                    )
                    return
            except Exception:
                pass

        # No reconnected worker found — reset the task
        await self._reset_orphaned_task(action.task_id, action.execution_id)

    async def _reset_orphaned_task(
        self, task_id: UUID, execution_id: UUID | None
    ) -> None:
        """Fail the execution and transition task back to ready_for_implementation."""
        logger.info(
            "recovery: resetting orphaned task=%s execution=%s to ready_for_implementation",
            task_id,
            execution_id,
        )

        if execution_id is not None:
            await self._store.append_event(
                aggregate_id=execution_id,
                aggregate_type="execution",
                event_type=ev.EXECUTION_FAILED,
                payload={
                    "execution_id": str(execution_id),
                    "failure_reason": "orchestrator_restart_recovery",
                    "status": "failed",
                },
            )

        sm = TaskStateMachine(self._store)
        await sm.transition(
            task_id,
            ev.READY_FOR_IMPLEMENTATION,
            extra_payload={"reason": "orchestrator_restart_recovery"},
        )
