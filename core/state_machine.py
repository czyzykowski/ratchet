"""Task state machine: validates transitions and persists state changes as events."""

from __future__ import annotations

from uuid import UUID

from core import events as ev
from core.models import Event
from core.store import Store

VALID_TRANSITIONS: dict[str, set[str]] = {
    ev.READY_FOR_SPEC: {ev.SPEC_QA},
    ev.SPEC_QA: {ev.READY_FOR_IMPLEMENTATION, ev.BLOCKED, ev.READY_FOR_SPEC},
    ev.READY_FOR_IMPLEMENTATION: {ev.IN_PROGRESS, ev.BLOCKED, ev.READY_FOR_SPEC},
    ev.IN_PROGRESS: {ev.BLOCKED, ev.READY_FOR_QA, ev.READY_FOR_SPEC},
    ev.READY_FOR_QA: {
        ev.READY_FOR_DEPLOYMENT, ev.BLOCKED, ev.READY_FOR_SPEC, ev.READY_FOR_IMPLEMENTATION
    },
    ev.READY_FOR_DEPLOYMENT: {
        ev.DEPLOYED, ev.BLOCKED, ev.READY_FOR_SPEC, ev.READY_FOR_IMPLEMENTATION
    },
    ev.BLOCKED: {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION},
    ev.DEPLOYED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when a requested status transition is not permitted."""


class TaskStateMachine:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def get_current_status(self, task_id: UUID) -> str | None:
        """Derive current task status from event history.

        Returns None if no events found for task_id.
        Replays TASK_CREATED and TASK_STATUS_CHANGED events in sequence order.
        """
        task_events = await self._store.get_events(task_id, "task")
        status: str | None = None
        for event in task_events:
            if event.event_type == ev.TASK_CREATED:
                status = event.payload.get("status", ev.READY_FOR_SPEC)
            elif event.event_type == ev.TASK_STATUS_CHANGED:
                status = event.payload["to_status"]
        return status

    async def transition(self, task_id: UUID, new_status: str) -> Event:
        """Validate and execute a status transition.

        Raises InvalidTransitionError if transition is not in the valid transition table.
        Appends TASK_STATUS_CHANGED event on success.
        Returns the appended event.
        """
        current = await self.get_current_status(task_id)
        if current is None:
            raise InvalidTransitionError(
                f"Task {task_id} not found — no events exist for this task_id"
            )
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition task {task_id} from {current!r} to {new_status!r}"
            )
        return await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            # "status" key required by current_tasks materialized view (payload->>'status')
            payload={"from_status": current, "to_status": new_status, "status": new_status},
        )
