# Spec: Switch worker from polling to Postgres LISTEN/NOTIFY

## Objective

Replace the 30-second sleep polling loop in `worker/runner.py` with a Postgres `LISTEN/NOTIFY`-driven event loop. The worker will react to task status changes in sub-second latency, queue notifications received during active execution, and perform a startup catchup query so no tasks are missed across restarts.

## Success Criteria
- [ ] A new Alembic migration adds a Postgres trigger on the `events` table that calls `pg_notify('ratchet_task_status', ...)` after every `task.status_changed` event insert
- [ ] Notification payload is a JSON string containing `task_id` and `status`
- [ ] Migration is reversible (`downgrade()` drops the trigger and trigger function)
- [ ] A new `NotificationListener` class in `worker/listener.py` opens a dedicated psycopg connection in autocommit mode, issues `LISTEN ratchet_task_status`, and yields parsed notifications via an async generator
- [ ] `NotificationListener` filters out statuses that are not `ready_for_implementation` or `ready_for_qa`; all other statuses are silently dropped
- [ ] `main_loop` in `worker/runner.py` is replaced by `notification_loop` that: (1) runs startup catchup by calling `run_once` and `run_qa_once` once before listening, (2) enters the notification-driven loop, (3) queues notifications received during execution using `asyncio.Queue`, (4) processes queued items after each task completes
- [ ] Worker capacity is parameterised (`max_workers: int = 1`); with `max_workers=1` only one task runs at a time and additional notifications are queued
- [ ] `main_loop_entry()` and `_main_loop_async()` in `runner.py` call `notification_loop` instead of the old `main_loop`
- [ ] Old `main_loop` (polling) is deleted
- [ ] `--once` path (`main()` / `_main_async()`) is unchanged
- [ ] Unit tests in `worker/tests/test_listener.py` verify: filter drops irrelevant statuses, correct statuses are passed through, queue holds notifications during active execution and drains after
- [ ] Existing tests in `worker/tests/test_loop.py` that test `main_loop` are updated or replaced to cover `notification_loop`

## Out of Scope
- Multi-worker / concurrent task execution (capacity > 1 is wired but not tested)
- Changes to `Store` protocol or `InMemoryStore`
- Changes to any script outside `worker/`

## Technical Context

**Current polling loop** (`worker/runner.py:445-458`): `main_loop` calls `run_once` and `run_qa_once` in a `while True` loop, sleeping 30 s only when both return `False`.

**Notification trigger**: Postgres `AFTER INSERT FOR EACH ROW` trigger on the `events` table, gated on `NEW.event_type = 'task.status_changed'`. Calls:
```sql
pg_notify(
  'ratchet_task_status',
  json_build_object(
    'task_id', NEW.aggregate_id::text,
    'status',  NEW.payload->>'to_status'
  )::text
)
```

**LISTEN connection**: psycopg v3 async connection in autocommit mode. Use `conn.notifies()` async generator (psycopg3 API). Must be separate from the pool used by `PostgresStore` — `LISTEN` state is per-connection and incompatible with pooled connections.

**Queue**: `asyncio.Queue()` (unbounded) holds `(task_id_str, status_str)` tuples. Notification coroutine puts items; execution coroutine gets items. With `max_workers=1` the notification coroutine still runs concurrently via `asyncio.gather` or `asyncio.create_task`, ensuring no notification is lost while a task executes.

**Migration chain**: latest migration is `f1a2b3c4d5e6` (`add_feature_materialized_views`). New migration `down_revision` must be `'f1a2b3c4d5e6'`.

**psycopg import**: `core/db.py` uses psycopg (v3). `worker/listener.py` must import psycopg lazily (inside the class or `__init__`) to keep test collection fast and consistent with the existing `PostgresStore` lazy-import pattern.

## Tasks
- [ ] Create Alembic migration `db/migrations/versions/XXXXXXXX_add_task_status_notify_trigger.py` with `down_revision = 'f1a2b3c4d5e6'`; `upgrade()` creates the PL/pgSQL trigger function `notify_task_status_changed()` and attaches it as trigger `trg_notify_task_status` on `events`; `downgrade()` drops both
- [ ] Create `worker/listener.py` with `NotificationListener` class: `__init__(self, dsn: str, max_workers: int = 1)`, async context manager `__aenter__`/`__aexit__`, method `listen(self) -> AsyncGenerator[tuple[str, str], None]` that connects, issues `LISTEN`, and yields `(task_id, status)` for `ready_for_implementation` and `ready_for_qa` notifications only
- [ ] Replace `main_loop` in `worker/runner.py` with `async def notification_loop(store: Store, invoker: ClaudeCodeInvoker, dsn: str, max_workers: int = 1) -> None`; implement startup catchup + queue-driven dispatch; delete old `main_loop`
- [ ] Update `_main_loop_async()` in `worker/runner.py` to read `DATABASE_URL` from environment, construct `NotificationListener`, and pass `dsn` to `notification_loop`
- [ ] Update `worker/tests/test_loop.py`: remove tests that reference `main_loop`; add tests for `notification_loop` using a mock `NotificationListener` that yields controlled notifications
- [ ] Create `worker/tests/test_listener.py`: unit tests for `NotificationListener.listen()` filtering logic using a mock psycopg connection

## Assumptions
- psycopg v3 is already a dependency (used by `PostgresStore` via `core/db.py`)
- `DATABASE_URL` environment variable is always set when running in loop mode (existing behaviour)
- The `events` table name is `events` (confirmed by migration `c67742c064f5_initial_schema.py`)
- `pg_notify` payload size stays well under 8000 bytes (task UUID + status string is ~80 bytes)
- asyncio event loop is already the runtime (worker is fully async)

## Verification Commands
```bash
# Unit tests (no DB required)
pytest worker/tests/test_listener.py worker/tests/test_loop.py -v

# Full unit test suite
pytest core/tests/ worker/tests/ -v

# Apply migration (requires DATABASE_URL)
alembic upgrade head

# Verify trigger exists
psql $DATABASE_URL -c "\df notify_task_status_changed"
psql $DATABASE_URL -c "\d+ events" | grep trg_notify

# Smoke test: trigger fires and worker wakes
# Terminal 1: start worker in loop mode
.venv/bin/python -m worker

# Terminal 2: advance a task to ready_for_implementation
.venv/bin/python scripts/add-task.py --project-id <uuid> --title "notify test"
# ... add spec, advance status ...
# Worker terminal should log "Starting execution" without waiting 30 s

# Rollback migration
alembic downgrade -1
psql $DATABASE_URL -c "\df notify_task_status_changed"  # should return nothing
```

## What Exists After This Spec

- `db/migrations/versions/XXXXXXXX_add_task_status_notify_trigger.py` — reversible migration with PL/pgSQL trigger
- `worker/listener.py` — `NotificationListener` async context manager
- `worker/runner.py` — `notification_loop` replaces `main_loop`; `run_once`, `run_qa_once`, `_main_async`, `main` unchanged
- `worker/tests/test_listener.py` — unit tests for listener filtering
- `worker/tests/test_loop.py` — updated to cover `notification_loop`
- The worker binary (`python -m worker`) reacts to task readiness in sub-second latency instead of up to 30 s