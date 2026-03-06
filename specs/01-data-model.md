# Spec 01: Core Data Model and Event Schema

## Objective

Create the foundational Python package structure for Ratchet and implement the event-sourced data model with Postgres, including materialized views for current state queries.

## Success Criteria

- [ ] Repository structure exists with `core/`, `worker/`, `tui/`, `db/`, `docs/` directories
- [ ] `db/` contains Alembic migration that creates all tables when run against a fresh Postgres database
- [ ] `core/` is importable as a Python package with no errors
- [ ] `core/models.py` defines all dataclasses/Pydantic models matching the event schema
- [ ] `core/events.py` defines all event types as an enum or constants
- [ ] `core/store.py` implements `append_event()` and `get_events()` functions
- [ ] `append_event()` writes a single event to the `events` table
- [ ] `get_events()` returns all events for a given `aggregate_id` ordered by `sequence`
- [ ] Materialized view `current_tasks` reflects current task state derived from events
- [ ] Materialized view `current_projects` reflects current project state derived from events
- [ ] `core/store.py` implements `refresh_views()` that refreshes all materialized views
- [ ] A smoke test script `db/smoke_test.py` appends 3 events and queries the materialized view successfully
- [ ] `pyproject.toml` exists at repo root with all dependencies declared
- [ ] `flake.nix` exists at repo root providing a working `devShell`
- [ ] `nix develop` enters a shell with python3.12, ruff, psql, pg_dump, alembic available
- [ ] All code passes `ruff` linting with no errors

## Out of Scope

- Do not implement state machine transition logic
- Do not implement any CLI, TUI, or API interface
- Do not implement worker or Claude Code invocation
- Do not implement knowledge layer or RAG
- Do not create FastAPI endpoints
- Do not implement worktree management
- Do not start, install, configure, or manage a Postgres instance
- Do not create Postgres users or databases — they are pre-existing
- Only create files in `core/`, `db/`, and repo root config files

## Repository Structure to Create

```
ratchet/
  core/
    __init__.py
    models.py        — Pydantic models for all domain entities
    events.py        — Event type definitions
    store.py         — append_event(), get_events(), refresh_views()
    db.py            — database connection and session management
  worker/
    __init__.py      — empty, placeholder
  tui/
    __init__.py      — empty, placeholder
  db/
    migrations/      — Alembic migrations directory
    alembic.ini
    smoke_test.py    — validation script
  docs/
    INTENT.md        — project intent statement
    CLAUDE.md        — technical entry point
  pyproject.toml
  .env.example
  flake.nix          — reproducible development shell
  flake.lock
```

## Technical Context

- Language: Python 3.12
- Database: PostgreSQL running on 127.0.0.1, database name `ratchet`, accessed via two users:
  - `ratchet` — application user for production use
  - `ratchet_test` — test user for smoke tests and future test suites
- Key libraries: Pydantic v2, psycopg[async], Alembic for migrations, ruff for linting
- Pattern: event sourcing — never update or delete rows, only append
- All domain state is derived from the `events` table via materialized views
- Development environment: Nix flake providing devShell — do not assume tools are globally installed
- Do not start, manage, or configure a Postgres instance — it is already running on 127.0.0.1

## Event Schema

### `events` table (append-only, never updated)

```
id              UUID, primary key, default gen_random_uuid()
aggregate_id    UUID, not null              — the entity this event belongs to (task, project, spec)
aggregate_type  VARCHAR(50), not null       — 'task' | 'project' | 'spec' | 'execution'
event_type      VARCHAR(100), not null      — see Event Types below
payload         JSONB, not null             — event-specific data
schema_version  INTEGER, not null, default 1
occurred_at     TIMESTAMPTZ, not null, default now()
sequence        BIGSERIAL                   — global ordering
```

### `projects` table (materialized view source)

Derived from project events. Materialized view `current_projects`:

```
id              UUID
name            VARCHAR(255)
repo_path       TEXT                        — absolute path to git repo
status          VARCHAR(50)                 — 'active' | 'archived'
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

### `tasks` table (materialized view source)

Derived from task events. Materialized view `current_tasks`:

```
id              UUID
project_id      UUID
title           TEXT
status          VARCHAR(50)                 — see Task Statuses below
current_spec_id UUID, nullable
refinement_count INTEGER, default 0
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

### `specs` table (materialized view source)

Derived from spec events. Materialized view `current_specs`:

```
id              UUID
task_id         UUID
previous_spec_id UUID, nullable            — lineage chain
content         TEXT                       — full spec content
created_at      TIMESTAMPTZ
```

### `executions` table (materialized view source)

Derived from execution events. Materialized view `current_executions`:

```
id              UUID
task_id         UUID
spec_id         UUID
status          VARCHAR(50)                — 'running' | 'completed' | 'failed'
failure_reason  TEXT, nullable
started_at      TIMESTAMPTZ
completed_at    TIMESTAMPTZ, nullable
```

## Event Types

```python
# Project events
PROJECT_CREATED = "project.created"
PROJECT_ARCHIVED = "project.archived"

# Task events
TASK_CREATED = "task.created"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_SPEC_ASSIGNED = "task.spec_assigned"

# Spec events
SPEC_CREATED = "spec.created"

# Execution events
EXECUTION_STARTED = "execution.started"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"
```

## Task Statuses

```python
READY_FOR_SPEC = "ready_for_spec"
SPEC_QA = "spec_qa"
READY_FOR_IMPLEMENTATION = "ready_for_implementation"
IN_PROGRESS = "in_progress"
BLOCKED = "blocked"
READY_FOR_QA = "ready_for_qa"
READY_FOR_DEPLOYMENT = "ready_for_deployment"
DEPLOYED = "deployed"
```

## Pydantic Models to Define in `core/models.py`

```python
class Event(BaseModel):
    id: UUID
    aggregate_id: UUID
    aggregate_type: str
    event_type: str
    payload: dict
    schema_version: int = 1
    occurred_at: datetime
    sequence: int

class Project(BaseModel):
    id: UUID
    name: str
    repo_path: str
    status: str
    created_at: datetime
    updated_at: datetime

class Task(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    status: str
    current_spec_id: UUID | None
    refinement_count: int
    created_at: datetime
    updated_at: datetime

class Spec(BaseModel):
    id: UUID
    task_id: UUID
    previous_spec_id: UUID | None
    content: str
    created_at: datetime

class Execution(BaseModel):
    id: UUID
    task_id: UUID
    spec_id: UUID
    status: str
    failure_reason: str | None
    started_at: datetime
    completed_at: datetime | None
```

## `core/store.py` Interface

```python
async def append_event(
    aggregate_id: UUID,
    aggregate_type: str,
    event_type: str,
    payload: dict,
    schema_version: int = 1
) -> Event:
    """Append a single event to the events table. Returns the written event."""

async def get_events(
    aggregate_id: UUID,
    aggregate_type: str | None = None
) -> list[Event]:
    """Return all events for aggregate_id ordered by sequence ascending."""

async def refresh_views() -> None:
    """Refresh all materialized views. Call after appending events."""
```

## INTENT.md Content to Create

```markdown
# Ratchet

## Purpose

Enable structured, autonomous AI-driven software development through a task orchestration
system that mirrors proven human development workflows.

## Problems It Solves

- Ad hoc AI-assisted development lacks structure, repeatability, and learning
- No mechanism to capture what works and what fails across executions
- Human involvement is reactive rather than deliberate

## Values

- Quality over speed — verify rather than trust
- Minimize human input — but never eliminate it where judgment is required
- Learn from every execution — success and failure both produce signal
- Incremental and irreversible progress — each completed task is permanent forward movement
```

## `flake.nix` devShell to Provide

```nix
# devShell must include:
# - python312
# - python312Packages.pip
# - ruff
# - postgresql_16 (client tools only: psql, pg_dump, pg_restore)
# - git
# Shell hook should warn if DATABASE_URL or TEST_DATABASE_URL are not set
# Shell hook must NOT attempt to start postgres
```

## Tasks

- [ ] Initialize git repository at `ratchet/`
- [ ] Create `flake.nix` with devShell as specified above
- [ ] Run `nix develop` and verify all required tools are available
- [ ] Create `pyproject.toml` with dependencies: pydantic>=2.0, psycopg[async], alembic, ruff, pytest, pytest-asyncio
- [ ] Create directory structure as specified above with placeholder `__init__.py` files
- [ ] Create `core/events.py` with all event type constants and task status constants
- [ ] Create `core/models.py` with all Pydantic models as specified
- [ ] Create `core/db.py` with async connection pool setup reading `DATABASE_URL` from environment
- [ ] Create `core/store.py` implementing `append_event()`, `get_events()`, `refresh_views()`
- [ ] Initialize Alembic in `db/` directory
- [ ] Write Alembic migration creating `events` table and all four materialized views
- [ ] Create `.env.example` with explicit connection strings:
  ```
  DATABASE_URL=postgresql+psycopg://ratchet@127.0.0.1:5432/ratchet
  TEST_DATABASE_URL=postgresql+psycopg://ratchet_test@127.0.0.1:5432/ratchet
  ```
- [ ] Create `db/smoke_test.py` that reads `TEST_DATABASE_URL`, appends a `PROJECT_CREATED` event, a `TASK_CREATED` event, a `TASK_STATUS_CHANGED` event, refreshes views, and queries `current_tasks` asserting one row exists
- [ ] Create `docs/INTENT.md` with content specified above
- [ ] Create `docs/CLAUDE.md` with pointers to pyproject.toml, core/, db/migrations/, and smoke_test.py
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Run `db/smoke_test.py` against local Postgres and confirm it passes

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- Database `ratchet` already exists
- Users `ratchet` and `ratchet_test` already exist with access to the `ratchet` database
- No passwords required — local trust or peer authentication assumed
- Smoke test runs as `ratchet_test` user via `TEST_DATABASE_URL`
- Alembic migrations run as `ratchet` user via `DATABASE_URL`
- Nix is installed and flakes are enabled in nix configuration
- No existing `ratchet/` directory — this is greenfield

## Verification Commands

```bash
ruff check .
python db/smoke_test.py
alembic upgrade head
```

## What Exists After This Spec

```
ratchet/
  core/           — importable Python package with models, events, store
  db/             — Alembic migrations, smoke test
  docs/           — INTENT.md, CLAUDE.md
  pyproject.toml
  flake.nix       — reproducible devShell
  .env.example
```

The events table exists in Postgres. Materialized views exist and are queryable.
No state machine logic, no UI, no worker. Foundation only.
