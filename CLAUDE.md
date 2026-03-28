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
  event_queries.py — has_pending_baseline_qa_failure (core event query helpers)
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
orchestrator/      — library code only (no entry point)
  registry.py      — WorkerRegistry, WorkerConnection
  dispatcher.py    — dispatch_pending, dispatch_loop
  sequencer.py     — PipelineSequencer (drives remote workers through multi-step commands)
  channel.py       — WebSocketWorkerChannel, PipelineAbort
  tests/           — orchestrator library tests
scripts/
  run-spec.sh      — execute a spec file via Claude Code
web/
  app.py           — FastAPI app with lifespan (registry, dispatch loop, local worker)
  local_worker.py  — LocalWorkerManager, spawns python -m worker --remote subprocess
  routes/api/
    ws_worker.py   — /ws/worker WebSocket endpoint for worker connections
    workers.py     — GET /api/workers (list connected workers from registry)
  tests/           — web API tests
worker/
  __init__.py      — exports LogBuffer, LogEntry only
  __main__.py      — CLI: requires --remote URL, connects to orchestrator
  remote.py        — RemoteWorker, connects to /ws/worker via WebSocket
  executor.py      — CommandExecutor, handles orchestrator command requests
  log_buffer.py    — LogBuffer ring buffer with pub/sub for worker log streaming
  worktree.py      — git worktree lifecycle helpers (safe_symlink used by executor)
  tests/           — worker tests (test_remote, test_executor, test_log_buffer)
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
- **SPA build**: Source is `web/spa/src/`, built with Vite to `web/spa/dist/`. The `dist/` directory is gitignored — rebuild after source changes with `cd web/spa && nix-shell -p nodejs --run "npm install && npm run build"`. Type-check separately with `npm run typecheck`. The FastAPI app serves `dist/` as the SPA at `/_app`.
- `python -m web` runs everything: web API on port 8000, `/ws/worker` WebSocket endpoint, orchestrator dispatch loop, and a local worker subprocess that connects back via WebSocket
- `LocalWorkerManager` spawns `python -m worker --remote ws://localhost:{port}/ws/worker` as a subprocess; controlled via `app.state.local_worker` (aliased as `app.state.worker_service`); configured by `WORKER_ENABLED`, `WORKER_CAPABILITIES`, `WEB_PORT` env vars
- `WorkerRegistry` (on `app.state.registry`) tracks all connected workers (local subprocess + any remote workers)
- Dispatch loop runs as async task in lifespan, controlled by `DISPATCH_ENABLED` env var (default `true`)
- Auto-merge: on each dispatch cycle, the worker attempts a local squash merge for one `ready_for_merge` task per project; `TASK_AUTO_MERGE_FAILED` prevents retry — use `scripts/merge-task.py` for manual merge
- **Workers run in remote mode only**: `python -m worker` requires `--remote <URL>`; the orchestrator's `dispatch_loop` discovers tasks and drives workers via WebSocket commands — there is no local dispatch fallback
- **Infrastructure changes break in-flight tasks**: Changes to shared modules (`core/remote_protocol.py`, `orchestrator/sequencer.py`, `worker/executor.py`, `ratchet.yaml`) affect ALL in-flight task executions because QA runs on worktrees that inherit from `develop`. The Claude QA fix loop cannot repair these errors because they are in develop code, not in the task's code. Always run the full QA gauntlet before committing: `ruff check core/ worker/ web/ orchestrator/ remote_worker/` and `mypy core/ worker/ web/ orchestrator/ remote_worker/` and `pytest core/tests/ web/tests/ worker/tests/`

## Running Things

**IMPORTANT: All unit tests use `InMemoryStore` and require NO database.** Do NOT run `db/smoke_test.py` or any command requiring `DATABASE_URL` / `TEST_DATABASE_URL` — those are integration tests for the orchestrator machine only. Workers do not have database access.

**IMPORTANT: Do NOT run `orchestrator/tests/test_channel.py`** — it hangs indefinitely due to async WebSocket mocking issues. Always exclude it with `--ignore`.

```bash
# Enter dev shell
nix develop

# Run ALL unit tests (no DB required)
.venv/bin/python -m pytest core/tests/ orchestrator/tests/ web/tests/ worker/tests/ --ignore=orchestrator/tests/test_channel.py -v

# Run tests for a specific module
.venv/bin/python -m pytest core/tests/ -v
.venv/bin/python -m pytest orchestrator/tests/ --ignore=orchestrator/tests/test_channel.py -v

# Apply migrations (orchestrator machine only, requires DATABASE_URL)
.venv/bin/alembic -c db/alembic.ini upgrade head

# Execute a spec
scripts/run-spec.sh specs/10-update-claude-md.md

# Build SPA (from web/spa/ — requires node via nix-shell)
cd web/spa
nix-shell -p nodejs --run "npm install && npm run build"
cd ../..

# Run web UI with orchestrator + local worker subprocess (single process)
.venv/bin/python -m web
# Optional flags:
.venv/bin/python -m web --port 9000 --no-dispatch

# Connect a remote worker to an orchestrator
.venv/bin/python -m worker --remote ws://localhost:8000/ws/worker --capabilities default

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
  board.py          — display task board grouped by status
  review-blocked.py — show blocked tasks with failure reasons
  archive-task.py   — abandon a task, transitioning it to the terminal 'abandoned' status
  unblock-task.py   — unblock a task, transitioning it directly back to ready_for_implementation; --retry-baseline clears pending baseline QA failures
  task-reset.py     — reset a task to ready_for_spec (or ready_for_implementation with --reuse-spec)
  merge-task.py     — manually squash-merge a task's execution branch into develop
  task-status.py    — detailed task inspector: execution history, failure reasons, worker assignment
  workers.py        — show connected workers with capabilities and current execution
  blocked-reasons.py — show all blocked tasks with categorized failure reasons
  diagnose.py       — comprehensive diagnostic: blocked tasks, orphaned executions, dependency chains, stale tasks; --fix-orphans to clean up; --task-id <uuid> for single task deep-dive
```

### Usage

```bash
# Register a project (repo_url and local_path both set to --path in v1)
python scripts/add-project.py --name <name> --path <repo_path>

# Create a task
python scripts/add-task.py --project-id <uuid> --title <title>

# Add a spec file and advance task to ready_for_implementation
python scripts/add-spec.py --task-id <uuid> --file <spec_file_path>

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

# Inspect task status, execution history, and failure reasons
python scripts/task-status.py                    # all active tasks
python scripts/task-status.py --task-id <uuid>   # specific task detail

# Show connected workers
python scripts/workers.py

# Diagnose stuck tasks (blocked, orphaned executions, dependency chains)
python scripts/diagnose.py                          # full diagnostic report
python scripts/diagnose.py --task-id <uuid>         # deep-dive single task
python scripts/diagnose.py --fix-orphans            # clean up orphaned running executions
```

### Full Workflow

```bash
# 1. Register your project
python scripts/add-project.py --name my-project --path $(pwd)

# 2. Create a task
python scripts/add-task.py --project-id <project-uuid> --title "My task"

# 3. Write a spec file and assign it
python scripts/add-spec.py --task-id <task-uuid> --file path/to/spec.md

# 4. Start the web process (runs orchestrator + local worker automatically)
.venv/bin/python -m web

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

## Design Context

### Users
Solo developer using Ratchet as a personal task orchestration dashboard for autonomous AI-driven software development. The user is highly technical, monitors task pipelines, reviews execution logs, and manages worker processes. Speed of comprehension and information density matter more than visual polish.

### Brand Personality
**Technical, precise, calm.** The interface should feel like a well-built developer tool — trustworthy, no-nonsense, and quietly competent. No marketing flair, no unnecessary decoration. Every element earns its place by conveying information.

### Aesthetic Direction
- **Polished modern** with **dashboard-grade data visualization** — elevate the current minimal/flat style with better typography, subtle depth, refined spacing, and richer status indicators
- Primary brand color: `#7b6cd8` (purple) — used for interactive elements, active states, and brand identity
- Semantic status palette already established (green/red/yellow/blue/cyan/purple mapped to task states) — preserve and refine these
- System font stack is fine; improve hierarchy through better weight/size/spacing choices
- Light mode primary; dark mode is not a current priority
- Flat design with subtle depth cues (refined shadows, borders) rather than heavy gradients or skeuomorphism
- Data-viz elements welcome: progress indicators, timeline views, richer status badges

### Design Principles

1. **Information density over whitespace** — This is a power-user tool. Maximize useful data per viewport without becoming cluttered. Compact is good; cramped is not.
2. **Status at a glance** — Task states, worker health, and execution progress should be immediately scannable. Use color, position, and shape consistently so the user never has to read a label to understand state.
3. **Calm confidence** — Avoid visual noise. Animations should be functional (loading, transitions), not decorative. The UI should feel stable and predictable.
4. **Progressive disclosure** — Show summary by default, detail on demand. The board view is the home base; drill-down views reveal execution logs, specs, and history.
5. **Consistency over novelty** — Every new component should reuse existing patterns (color tokens, spacing scale, border radius, button styles). When in doubt, match what exists.
