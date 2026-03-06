# Spec 02: Store Protocol and Task State Machine

## Objective

Refactor the event store into a pluggable protocol with PostgreSQL and in-memory implementations, then implement the task state machine that validates transitions and persists state changes as events.

## Success Criteria

- [ ] `core/store.py` defines a `Store` protocol with `append_event()`, `get_events()`, `refresh_views()` method signatures
- [ ] `core/store.py` implements `InMemoryStore` satisfying the `Store` protocol
- [ ] `core/store.py` implements `PostgresStore` satisfying the `Store` protocol — functionally identical to the free functions from spec 01
- [ ] `mypy` or `pyright` confirms both stores satisfy the protocol (no type errors)
- [ ] `core/state_machine.py` implements `TaskStateMachine` class accepting a `Store` instance
- [ ] `TaskStateMachine.transition(task_id, new_status)` raises `InvalidTransitionError` for invalid transitions
- [ ] `TaskStateMachine.transition(task_id, new_status)` appends a `TASK_STATUS_CHANGED` event for valid transitions
- [ ] All valid transitions from the transition table below are accepted
- [ ] All invalid transitions raise `InvalidTransitionError`
- [ ] `core/state_machine.py` implements `get_current_status(task_id)` returning current task status derived from events
- [ ] Unit tests in `core/tests/test_state_machine.py` use `InMemoryStore` exclusively — no database required
- [ ] Unit tests cover every valid transition
- [ ] Unit tests cover at least 5 invalid transitions including attempts to go backwards
- [ ] Unit tests cover transitioning an unknown task_id
- [ ] `db/smoke_test.py` is updated to use `PostgresStore` instance instead of free functions
- [ ] All existing smoke test assertions still pass after refactor
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/` passes with no errors and no database connection required

## Out of Scope

- Do not implement task creation — only status transitions on existing tasks
- Do not implement spec assignment logic
- Do not implement worker or Claude Code invocation
- Do not implement TUI or API
- Do not modify the database schema or migrations
- Do not implement project-level state machine — tasks only
- Do not modify `core/models.py` or `core/events.py`
- Only modify `core/store.py`, create `core/state_machine.py`, create `core/tests/`, update `db/smoke_test.py`

## Technical Context

- Language: Python 3.12
- Pattern: `typing.Protocol` for structural subtyping — stores are duck-typed, not inherited
- State machine receives a `Store` instance — never imports a concrete store directly
- `InMemoryStore` is the reference implementation — if behavior differs between stores, in-memory is correct
- Existing files from spec 01:
  - `core/store.py` — free functions to be refactored into classes
  - `core/events.py` — event type constants and task status constants
  - `core/models.py` — Pydantic models including `Event`
  - `core/db.py` — Postgres connection pool
  - `db/migrations/` — existing schema, do not modify

## Valid Transition Table

```
FROM                     → TO
─────────────────────────────────────────────────────
ready_for_spec           → spec_qa
spec_qa                  → ready_for_implementation
spec_qa                  → blocked
ready_for_implementation → in_progress
ready_for_implementation → blocked
in_progress              → blocked
in_progress              → ready_for_qa
ready_for_qa             → ready_for_deployment
ready_for_qa             → blocked
ready_for_deployment     → deployed
ready_for_deployment     → blocked
blocked                  → ready_for_spec
blocked                  → spec_qa
blocked                  → ready_for_implementation
```

All other transitions are invalid and must raise `InvalidTransitionError`.

## Store Protocol to Define

```python
class Store(Protocol):
    async def append_event(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict,
        schema_version: int = 1
    ) -> Event: ...

    async def get_events(
        self,
        aggregate_id: UUID,
        aggregate_type: str | None = None
    ) -> list[Event]: ...

    async def refresh_views(self) -> None: ...
```

## InMemoryStore Behavior

```python
class InMemoryStore:
    # Stores events in a list in memory
    # append_event() assigns an incrementing sequence number starting at 1
    # get_events() filters by aggregate_id, optionally by aggregate_type
    # refresh_views() is a no-op — no views to refresh in memory
    # Each InMemoryStore instance is isolated — no shared state between instances
```

## TaskStateMachine Interface

```python
class InvalidTransitionError(Exception):
    """Raised when a requested status transition is not permitted."""

class TaskStateMachine:
    def __init__(self, store: Store) -> None: ...

    async def transition(
        self,
        task_id: UUID,
        new_status: str
    ) -> Event:
        """
        Validate and execute a status transition.
        Raises InvalidTransitionError if transition is not in the valid transition table.
        Appends TASK_STATUS_CHANGED event on success.
        Returns the appended event.
        """

    async def get_current_status(
        self,
        task_id: UUID
    ) -> str | None:
        """
        Derive current task status from event history.
        Returns None if no events found for task_id.
        Replays TASK_CREATED and TASK_STATUS_CHANGED events in sequence order.
        """
```

## TASK_STATUS_CHANGED Event Payload

```python
{
    "from_status": "in_progress",   # previous status
    "to_status": "ready_for_qa",    # new status
}
```

## Test Scenarios to Cover in `core/tests/test_state_machine.py`

```
Valid transitions:
- ready_for_spec → spec_qa
- spec_qa → ready_for_implementation
- spec_qa → blocked
- in_progress → ready_for_qa
- in_progress → blocked
- ready_for_deployment → deployed
- blocked → ready_for_spec
- blocked → ready_for_implementation

Invalid transitions (must raise InvalidTransitionError):
- ready_for_spec → in_progress (skipping stages)
- in_progress → ready_for_spec (backwards)
- deployed → in_progress (from terminal state)
- spec_qa → deployed (skipping all stages)
- ready_for_qa → in_progress (backwards)

Edge cases:
- get_current_status() on unknown task_id returns None
- transition() on unknown task_id raises InvalidTransitionError
- Multiple sequential transitions on same task_id produce correct final status
```

## Tasks

- [ ] Add `pytest` and `pytest-asyncio` to `pyproject.toml` if not already present
- [ ] Refactor `core/store.py`: define `Store` protocol, implement `InMemoryStore`, convert existing free functions into `PostgresStore` class
- [ ] Verify `PostgresStore` is a drop-in replacement — same behavior as spec 01 free functions
- [ ] Create `core/tests/__init__.py`
- [ ] Create `core/state_machine.py` with `InvalidTransitionError`, `TaskStateMachine` as specified
- [ ] Implement `get_current_status()` by replaying events — do not query materialized views
- [ ] Implement `transition()` with valid transition table lookup and event appending
- [ ] Create `core/tests/test_state_machine.py` using `InMemoryStore`, covering all scenarios above
- [ ] Update `db/smoke_test.py` to instantiate `PostgresStore` and pass it to `TaskStateMachine`
- [ ] Run `pytest core/tests/` and confirm all tests pass with no database connection
- [ ] Run `python db/smoke_test.py` and confirm it still passes
- [ ] Run `ruff check .` and fix all linting errors

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- `DATABASE_URL` and `TEST_DATABASE_URL` environment variables are set
- `TASK_CREATED` events include `{"status": "ready_for_spec"}` in payload — this is the initial status used by `get_current_status()` when replaying history
- Tasks always start in `ready_for_spec` status

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
python db/smoke_test.py
```

## What Exists After This Spec

```
core/
  store.py          — Store protocol, InMemoryStore, PostgresStore
  state_machine.py  — InvalidTransitionError, TaskStateMachine
  tests/
    __init__.py
    test_state_machine.py — full unit test suite, no DB required
db/
  smoke_test.py     — updated to use PostgresStore + TaskStateMachine
```

State machine logic is fully tested in isolation. Valid and invalid transitions are defined and enforced. Store abstraction is in place for all future specs.
