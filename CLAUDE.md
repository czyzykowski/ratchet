# Ratchet

## What This Is

Task orchestration system for autonomous AI-driven software development.
See `docs/INTENT.md` for project purpose and values.

## Repo Structure

```
core/
  events.py        — event type constants, task status constants
  models.py        — Pydantic models: Event, Project, Task, Spec, Execution
  store.py         — Store protocol, InMemoryStore, PostgresStore
  state_machine.py — InvalidTransitionError, TaskStateMachine
  spec_manager.py  — SpecManager
  db.py            — async connection pool, reads DATABASE_URL from environment
  execution_manager.py — ExecutionManager, prepare/cleanup worktree environment
  context_assembler.py — ContextAssembler, ExecutionContext, assembles Claude Code prompt
  invoker.py           — ClaudeCodeInvoker, runs Claude Code subprocess, captures traces
  tests/           — unit tests, no database required
db/
  migrations/      — Alembic migrations (alembic upgrade head)
  smoke_test.py    — integration test, requires TEST_DATABASE_URL
docs/
  INTENT.md        — project intent statement (injected by orchestrator at runtime)
scripts/
  run-spec.sh      — execute a spec file via Claude Code
worker/
  __init__.py
  __main__.py     — enables python -m worker
  runner.py       — get_next_task, run_once, main — single-pass task executor
  service.py      — WorkerService, managed async lifecycle for embedded worker
  log_buffer.py   — LogBuffer ring buffer with pub/sub for worker log streaming
  tests/          — integration tests
pyproject.toml     — dependencies
flake.nix          — reproducible dev shell (nix develop)
.env.example       — connection string templates
```

## Database

- Postgres on 127.0.0.1:5432, database `ratchet`
- Application user: `ratchet` (used by alembic and PostgresStore)
- Test user: `ratchet_test` (used by smoke_test.py)
- Do NOT start or configure Postgres — it runs natively on NixOS
- Environment variables: `DATABASE_URL`, `TEST_DATABASE_URL`

## Key Patterns

- All core modules accept a `Store` instance — never import a concrete store directly
- `InMemoryStore` is used in all unit tests — fast, no database required
- `PostgresStore` uses lazy import of `core.db` to avoid psycopg import during test collection
- Events are append-only — never update or delete rows in the `events` table
- State is always derived from event replay — do not trust materialized views for correctness
- **Read path separation**: `web/routes/` handlers must read data via `web/queries.py` (materialized view queries), NOT via `core/` managers (event replay). `core/` managers are for the worker/dispatch path where real-time correctness matters. Do not import `TaskManager`, `SpecManager`, `ExecutionManager`, `ProjectManager`, or `FeatureManager` in `web/routes/` for read operations. Writes (state transitions) may still use `core/state_machine.py` directly.
- `ContextAssembler` assembles execution prompt from INTENT.md, spec content, and knowledge placeholder
- `ClaudeCodeInvoker` runs `claude -p <prompt>` as subprocess in worktree, detects COMPLETED/BLOCKED markers
- Traces written to `$XDG_DATA_HOME/ratchet/traces/` (default `~/.local/share/ratchet/traces/`)
- `flake.nix` shellHook sets `LD_LIBRARY_PATH` for libpq — required for psycopg to find PostgreSQL client library
- All scripts must be run with `.venv/bin/python` — system Python does not have dependencies
- The embedded worker in the web process shares its connection pool and is controlled via `app.state.worker_service`; configured by `WORKER_ENABLED`, `WORKER_WATCHDOG_TIMEOUT`, `WORKER_MAX_WORKERS`, `WORKER_CAPABILITIES` env vars
- Auto-merge: on each dispatch cycle, the worker attempts a local squash merge for one `ready_for_merge` task per project; `TASK_AUTO_MERGE_FAILED` prevents retry — use `scripts/merge-task.py` for manual merge

## Running Things

```bash
# Enter dev shell
nix develop

# Run unit tests (no DB required)
pytest core/tests/ -v

# Run integration smoke test
python db/smoke_test.py

# Apply migrations
.venv/bin/alembic -c db/alembic.ini upgrade head

# Execute a spec
scripts/run-spec.sh specs/10-update-claude-md.md

# Run worker single pass (dispatch priority: QA → merge → implementation → compilation)
.venv/bin/python -m worker

# Run web UI with embedded worker (starts both uvicorn and worker service)
.venv/bin/python -m web

# Run operational scripts (must use .venv/bin/python)
.venv/bin/python scripts/board.py
.venv/bin/python scripts/add-task.py --project-id <uuid> --title "My task"
```

## Operational Scripts

```
scripts/
  add-project.py    — register a repo as a managed project
  add-task.py       — create a task in ready_for_spec status
  add-spec.py       — add and assign a spec, advance task to ready_for_implementation
  run-next.py       — execute next ready task via worker
  board.py          — display task board grouped by status
  review-blocked.py — show blocked tasks with failure reasons
  archive-task.py   — abandon a task, transitioning it to the terminal 'abandoned' status
  unblock-task.py   — unblock a task, transitioning it directly back to ready_for_implementation; --retry-baseline clears pending baseline QA failures
  task-reset.py     — reset a task to ready_for_spec (or ready_for_implementation with --reuse-spec)
  merge-task.py     — manually squash-merge a task's execution branch into develop
```

### Usage

```bash
# Register a project (repo_url and local_path both set to --path in v1)
python scripts/add-project.py --name <name> --path <repo_path>

# Create a task
python scripts/add-task.py --project-id <uuid> --title <title>

# Add a spec file and advance task to ready_for_implementation
python scripts/add-spec.py --task-id <uuid> --file <spec_file_path>

# Run the next ready_for_implementation task
python scripts/run-next.py
#   or equivalently:
python -m worker

# View task board
python scripts/board.py

# Review blocked tasks with failure context
python scripts/review-blocked.py

# Abandon a task
python scripts/archive-task.py --task-id <uuid> [--reason "reason text"]

# Unblock a task (direct blocked → ready_for_implementation)
python scripts/unblock-task.py --task-id <uuid>
# Clear a pending baseline QA failure so the worker will retry baseline check
python scripts/unblock-task.py --task-id <uuid> --retry-baseline

# Reset a task to ready_for_spec (or ready_for_implementation reusing existing spec)
python scripts/task-reset.py --task-id <uuid>
python scripts/task-reset.py --task-id <uuid> --reuse-spec

# Manually merge a task (fallback when auto-merge fails)
python scripts/merge-task.py --task-id <uuid>
```

### Full Workflow

```bash
# 1. Register your project
python scripts/add-project.py --name my-project --path $(pwd)

# 2. Create a task
python scripts/add-task.py --project-id <project-uuid> --title "My task"

# 3. Write a spec file and assign it
python scripts/add-spec.py --task-id <task-uuid> --file path/to/spec.md

# 4. Run the worker to execute the task
python scripts/run-next.py
#   or equivalently:
python -m worker

# 5. Check the board
python scripts/board.py

# 6. If a task is blocked, review failure reasons and re-assign spec
python scripts/review-blocked.py
python scripts/add-spec.py --task-id <task-uuid> --file path/to/revised-spec.md
```

All scripts read `DATABASE_URL` from environment and exit 1 with a clear message if not set.

## QA Pipeline

The `ratchet.yaml` file defines the QA steps that run on every task execution:

- `test`: `.venv/bin/python -m pytest core/tests/ web/tests/ -v`
- `typecheck`: `mypy core/ worker/ web/`
- `lint`: `ruff check .`

**IMPORTANT:** Any new top-level module directory must have its `tests/` added to both the
`test` and `typecheck` steps in `ratchet.yaml`. Failure to do so means the new module's
tests will never run in QA and type errors will go undetected.

## Commit Convention

**CRITICAL: You MUST commit before outputting COMPLETED.**
The QA pipeline checks the git diff on your execution branch. If you do not commit,
your changes are invisible to QA and the task will be marked as failed.

After completing all tasks in a spec, commit with:

```bash
git add -A && git commit -m "feat: <brief description>"
```

Then verify the commit exists before declaring done:

```bash
git log --oneline -3
```

Every spec execution must end with a commit if all tasks succeeded.

## Merge Convention

**CRITICAL: After every merge to develop, push to remote immediately:**

```bash
git push origin develop
```

Never leave merged changes unpushed.
