"""Spec manager: creates specs, manages lineage chains, and tracks current assignment."""

from __future__ import annotations

from uuid import UUID, uuid4

from core import events as ev
from core.models import Event, Spec
from core.store import Store


class SpecManager:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def create_spec(
        self,
        task_id: UUID,
        content: str,
        previous_spec_id: UUID | None = None,
    ) -> Spec:
        """Create a new spec for a task.

        Appends SPEC_CREATED event under both the spec aggregate (for get_spec lookups)
        and the task aggregate (for lineage lookups).
        Does not automatically assign the spec — call assign_spec() separately.
        """
        spec_id = uuid4()
        payload = {
            "spec_id": str(spec_id),
            "task_id": str(task_id),
            "content": content,
            "previous_spec_id": str(previous_spec_id) if previous_spec_id else None,
        }
        # Store under spec_id so get_spec(spec_id) can retrieve it directly.
        event = await self._store.append_event(
            aggregate_id=spec_id,
            aggregate_type="spec",
            event_type=ev.SPEC_CREATED,
            payload=payload,
        )
        # Store under task_id so get_spec_lineage(task_id) can find all specs for the task.
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_spec",
            event_type=ev.SPEC_CREATED,
            payload=payload,
        )
        return Spec(
            id=spec_id,
            task_id=task_id,
            previous_spec_id=previous_spec_id,
            content=content,
            created_at=event.occurred_at,
        )

    async def assign_spec(self, task_id: UUID, spec_id: UUID) -> Event:
        """Assign a spec as the current spec for a task.

        Appends TASK_SPEC_ASSIGNED event.
        Returns the appended event.
        """
        current = await self.get_current_spec(task_id)
        previous_spec_id = current.id if current is not None else None
        return await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_SPEC_ASSIGNED,
            payload={
                "spec_id": str(spec_id),
                "previous_spec_id": str(previous_spec_id) if previous_spec_id else None,
            },
        )

    async def get_current_spec(self, task_id: UUID) -> Spec | None:
        """Return the currently assigned spec for a task.

        Replays TASK_SPEC_ASSIGNED events — most recent assignment wins.
        Returns None if no spec has been assigned.
        """
        task_events = await self._store.get_events(task_id, "task")
        current_spec_id: UUID | None = None
        for event in task_events:
            if event.event_type == ev.TASK_SPEC_ASSIGNED:
                current_spec_id = UUID(event.payload["spec_id"])
        if current_spec_id is None:
            return None
        return await self.get_spec(current_spec_id)

    async def get_spec(self, spec_id: UUID) -> Spec | None:
        """Return a spec by id.

        Finds the SPEC_CREATED event stored under spec aggregate.
        Returns None if not found.
        """
        spec_events = await self._store.get_events(spec_id, "spec")
        for event in spec_events:
            if event.event_type == ev.SPEC_CREATED:
                p = event.payload
                return Spec(
                    id=UUID(p["spec_id"]),
                    task_id=UUID(p["task_id"]),
                    previous_spec_id=UUID(p["previous_spec_id"]) if p["previous_spec_id"] else None,
                    content=p["content"],
                    created_at=event.occurred_at,
                )
        return None

    async def get_spec_lineage(self, task_id: UUID) -> list[Spec]:
        """Return all specs for a task in lineage order — oldest first.

        Reconstructs chain by following previous_spec_id links.
        Returns empty list if no specs exist for task.
        """
        task_spec_events = await self._store.get_events(task_id, "task_spec")
        specs: list[Spec] = []
        for event in task_spec_events:
            if event.event_type == ev.SPEC_CREATED:
                p = event.payload
                specs.append(
                    Spec(
                        id=UUID(p["spec_id"]),
                        task_id=UUID(p["task_id"]),
                        previous_spec_id=(
                            UUID(p["previous_spec_id"]) if p["previous_spec_id"] else None
                        ),
                        content=p["content"],
                        created_at=event.occurred_at,
                    )
                )
        if not specs:
            return []

        specs_by_id = {s.id: s for s in specs}
        # Find head: the spec that no other spec points to as previous_spec_id.
        previous_ids = {s.previous_spec_id for s in specs if s.previous_spec_id is not None}
        heads = [s for s in specs if s.id not in previous_ids]
        # Walk backwards from head to build oldest-first chain.
        head = heads[0]
        chain: list[Spec] = []
        current: Spec | None = head
        while current is not None:
            chain.append(current)
            prev_id = current.previous_spec_id
            current = specs_by_id.get(prev_id) if prev_id is not None else None
        chain.reverse()
        return chain
