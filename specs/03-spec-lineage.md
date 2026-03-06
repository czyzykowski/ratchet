# Spec 03: Spec Entity and Lineage Chain

## Objective

Implement the spec entity management layer — creating specs, linking them into an immutable lineage chain per task, and assigning the current spec to a task — as a fully tested core module.

## Success Criteria

- [ ] `core/spec_manager.py` implements `SpecManager` class accepting a `Store` instance
- [ ] `SpecManager.create_spec(task_id, content, previous_spec_id=None)` appends a `SPEC_CREATED` event and returns a `Spec` model
- [ ] `SpecManager.assign_spec(task_id, spec_id)` appends a `TASK_SPEC_ASSIGNED` event and returns the event
- [ ] `SpecManager.get_current_spec(task_id)` returns the `Spec` for the current `spec_id` on the task, or `None` if none assigned
- [ ] `SpecManager.get_spec_lineage(task_id)` returns ordered list of all specs for a task, oldest first, following `previous_spec_id` chain
- [ ] `SpecManager.get_spec(spec_id)` returns a `Spec` by id or `None` if not found
- [ ] Lineage chain is immutable — specs are never updated or deleted, only new ones created
- [ ] `refinement_count` on a task is derivable from lineage chain length — `len(get_spec_lineage(task_id))`
- [ ] Unit tests in `core/tests/test_spec_manager.py` use `InMemoryStore` exclusively
- [ ] Unit tests cover creating a first spec with no previous
- [ ] Unit tests cover creating a second spec linked to first — lineage chain of length 2
- [ ] Unit tests cover creating a third spec linked to second — lineage chain of length 3, oldest first
- [ ] Unit tests cover `get_current_spec()` returning correct spec after assignment
- [ ] Unit tests cover `get_current_spec()` returning `None` when no spec assigned
- [ ] Unit tests cover `get_spec()` returning `None` for unknown spec_id
- [ ] Unit tests cover that `get_spec_lineage()` returns specs in correct order regardless of event insertion order
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ -v` passes with no errors and no database connection required

## Out of Scope

- Do not implement spec content validation or quality checks — content is stored as-is
- Do not implement spec QA logic — that is a future spec
- Do not modify the database schema or migrations
- Do not implement state machine transitions — use existing `TaskStateMachine` for that
- Do not implement execution logic
- Do not modify `core/store.py`, `core/state_machine.py`, or `core/models.py`
- Do not implement TUI, API, or worker
- Only create `core/spec_manager.py` and `core/tests/test_spec_manager.py`

## Technical Context

- Language: Python 3.12
- Pattern: same as `TaskStateMachine` — accepts a `Store` instance, never imports concrete store
- Existing files:
  - `core/store.py` — `Store` protocol, `InMemoryStore`, `PostgresStore`
  - `core/events.py` — `SPEC_CREATED`, `TASK_SPEC_ASSIGNED` event type constants
  - `core/models.py` — `Spec`, `Task`, `Event` Pydantic models
  - `core/state_machine.py` — reference pattern for how to build on top of Store
  - `core/tests/test_state_machine.py` — reference pattern for unit tests using InMemoryStore

## SPEC_CREATED Event Payload

```python
{
    "spec_id": str(uuid),           # id of this spec
    "task_id": str(task_id),        # task this spec belongs to
    "content": "full spec text",    # full markdown content
    "previous_spec_id": str(uuid) | None  # None for first spec on a task
}
```

## TASK_SPEC_ASSIGNED Event Payload

```python
{
    "spec_id": str(uuid),           # spec being assigned as current
    "previous_spec_id": str(uuid) | None  # spec that was previously assigned, None if first
}
```

## SpecManager Interface

```python
class SpecManager:
    def __init__(self, store: Store) -> None: ...

    async def create_spec(
        self,
        task_id: UUID,
        content: str,
        previous_spec_id: UUID | None = None
    ) -> Spec:
        """
        Create a new spec for a task.
        Appends SPEC_CREATED event.
        Does not automatically assign the spec to the task — call assign_spec() separately.
        Returns the created Spec.
        """

    async def assign_spec(
        self,
        task_id: UUID,
        spec_id: UUID
    ) -> Event:
        """
        Assign a spec as the current spec for a task.
        Appends TASK_SPEC_ASSIGNED event.
        Returns the appended event.
        """

    async def get_current_spec(
        self,
        task_id: UUID
    ) -> Spec | None:
        """
        Return the currently assigned spec for a task.
        Derived by replaying TASK_SPEC_ASSIGNED events — most recent assignment wins.
        Returns None if no spec has been assigned.
        """

    async def get_spec(
        self,
        spec_id: UUID
    ) -> Spec | None:
        """
        Return a spec by id.
        Derived by finding SPEC_CREATED event with matching spec_id in payload.
        Returns None if not found.
        """

    async def get_spec_lineage(
        self,
        task_id: UUID
    ) -> list[Spec]:
        """
        Return all specs for a task in lineage order — oldest first.
        Reconstructs chain by following previous_spec_id links.
        Returns empty list if no specs exist for task.
        """
```

## Lineage Chain Reconstruction

`get_spec_lineage()` must follow the `previous_spec_id` chain, not rely on event order:

```
spec_C.previous_spec_id → spec_B
spec_B.previous_spec_id → spec_A
spec_A.previous_spec_id → None

Result: [spec_A, spec_B, spec_C]  # oldest first
```

This ensures correct ordering even if events were appended out of order.

## Test Scenarios to Cover

```
Lineage:
- Create spec with no previous → lineage length 1
- Create second spec linked to first → lineage [first, second]
- Create third spec linked to second → lineage [first, second, third]
- get_spec_lineage() on task with no specs → empty list

Assignment:
- get_current_spec() before any assignment → None
- assign_spec() then get_current_spec() → returns correct spec
- assign second spec → get_current_spec() returns second spec, not first
- get_spec() with unknown spec_id → None

Combined:
- create_spec() does not automatically assign — get_current_spec() still None after create
- Full workflow: create spec A → assign A → create spec B (previous=A) → assign B
  → get_current_spec() returns B, get_spec_lineage() returns [A, B]
```

## Tasks

- [ ] Create `core/spec_manager.py` with `SpecManager` class as specified
- [ ] Implement `create_spec()` — generate new UUID for spec, append `SPEC_CREATED` event, return `Spec` model
- [ ] Implement `assign_spec()` — find current spec_id before assigning (for payload), append `TASK_SPEC_ASSIGNED` event
- [ ] Implement `get_spec()` — replay all events, find `SPEC_CREATED` with matching spec_id in payload
- [ ] Implement `get_current_spec()` — replay `TASK_SPEC_ASSIGNED` events for task_id, return spec for most recent assignment
- [ ] Implement `get_spec_lineage()` — collect all specs for task, reconstruct chain via previous_spec_id links
- [ ] Create `core/tests/test_spec_manager.py` covering all scenarios above using `InMemoryStore`
- [ ] Run `pytest core/tests/ -v` and confirm all tests pass with no database connection
- [ ] Run `ruff check .` and fix all linting errors

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- `InMemoryStore` from spec 02 is the correct store to use in tests
- Caller is responsible for passing a valid `previous_spec_id` — `SpecManager` does not validate that it exists
- Caller is responsible for calling `assign_spec()` after `create_spec()` — they are intentionally separate operations
- A task can have multiple specs created but only one assigned at a time

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
```

## What Exists After This Spec

```
core/
  spec_manager.py   — SpecManager with full lineage chain support
  tests/
    test_state_machine.py  — unchanged
    test_spec_manager.py   — full unit test suite, no DB required
```

Spec creation, assignment, and lineage reconstruction are fully implemented and tested in isolation. No database required for tests. Foundation for execution tracking in spec 04.
