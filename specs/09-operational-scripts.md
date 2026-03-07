# Spec 09: Operational Scripts and Migration Fix

## Objective

Fix the `current_projects` materialized view to reflect the `repo_url`/`local_path` model change, then implement all six operational scripts that make Ratchet usable end-to-end: register a project, create tasks, add specs, run the worker, view the board, and review blocked tasks.

## Success Criteria

### Migration

- [ ] New Alembic migration adds `repo_url` and `local_path` columns to `current_projects` view, removing `repo_path`
- [ ] `alembic upgrade head` runs cleanly against local Postgres
- [ ] `python db/smoke_test.py` still passes after migration

### Scripts

- [ ] `scripts/add-project.py` registers a project and prints project id and name
- [ ] `scripts/add-project.py` accepts `--name` and `--path` arguments
- [ ] `scripts/add-project.py` prints clear error if onboarding validation fails
- [ ] `scripts/add-task.py` creates a task and prints task id
- [ ] `scripts/add-task.py` accepts `--project-id` and `--title` arguments
- [ ] `scripts/add-spec.py` creates a spec, assigns it, transitions task to `ready_for_implementation`
- [ ] `scripts/add-spec.py` accepts `--task-id` and `--file` arguments (`--file` reads spec content from file path)
- [ ] `scripts/add-spec.py` prints spec id and confirms task is now `ready_for_implementation`
- [ ] `scripts/run-next.py` invokes worker and exits with worker's exit code
- [ ] `scripts/board.py` prints all tasks grouped by status across all projects
- [ ] `scripts/board.py` shows for each task: id (short), title, project name, refinement count
- [ ] `scripts/board.py` shows empty board message if no tasks exist
- [ ] `scripts/review-blocked.py` prints all blocked tasks with failure reasons
- [ ] `scripts/review-blocked.py` shows task id, title, project name, current spec id, failure reason from most recent failed execution
- [ ] `scripts/review-blocked.py` shows "No blocked tasks." if none exist
- [ ] All scripts read `DATABASE_URL` from environment
- [ ] All scripts print usage and exit 1 if required arguments missing
- [ ] All scripts handle `OnboardingError`, `InvalidTransitionError`, `ContextAssemblyError` gracefully — print error and exit 1

### CLAUDE.md

- [ ] CLAUDE.md updated to document all six scripts with usage examples
- [ ] CLAUDE.md documents worker invocation via `python -m worker`
- [ ] CLAUDE.md documents full workflow: add-project → add-task → add-spec → run-next → board → review-blocked

### General

- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ worker/tests/ -v` still passes — no regressions
- [ ] `python scripts/board.py` runs against real Postgres and exits cleanly
- [ ] Commit: `git add -A && git commit -m "spec(09): operational scripts and migration fix"`

## Out of Scope

- Do not implement TUI
- Do not implement polling or daemon mode
- Do not implement knowledge extraction
- Do not add new core modules
- Do not modify existing core modules
- Scripts are thin wrappers over core layer — no new business logic in scripts

## Technical Context

- Language: Python 3.12
- Scripts import core layer directly — no HTTP, no subprocess
- All scripts are async — use `asyncio.run()` as entry point
- Scripts use `argparse` for argument parsing
- `DATABASE_URL` read from environment — scripts fail fast with clear message if not set
- Existing scripts directory already contains `run-spec.sh` — add Python scripts alongside it
- Short task id display: first 8 characters of UUID is sufficient for board display

## Migration Details

The `current_projects` materialized view was created in the initial migration with a `repo_path` column. The `Project` model was updated in spec 05 to use `repo_url` and `local_path`. A new migration must drop and recreate the view with the correct columns.

```sql
-- Drop existing view
DROP MATERIALIZED VIEW IF EXISTS current_projects;

-- Recreate with correct columns derived from PROJECT_CREATED event payload
CREATE MATERIALIZED VIEW current_projects AS
SELECT
    (payload->>'project_id')::uuid AS id,
    payload->>'name' AS name,
    payload->>'repo_url' AS repo_url,
    payload->>'local_path' AS local_path,
    payload->>'status' AS status,
    occurred_at AS created_at,
    occurred_at AS updated_at
FROM events
WHERE aggregate_type = 'project'
  AND event_type = 'project.created'
WITH NO DATA;
```

Note: status updates from `PROJECT_ARCHIVED` events are not yet reflected in the view — archived projects still appear. This is acceptable for v1 and can be improved in a future migration.

## Script Interfaces

### `scripts/add-project.py`

```
Usage: python scripts/add-project.py --name <name> --path <repo_path>

Registers a local git repository as a Ratchet-managed project.
repo_path must contain CLAUDE.md at root and docs/INTENT.md.
In v1, repo_url and local_path are set to the same value (repo_path).

Output:
  Registered project: <name>
  Project ID: <uuid>
  Repo: <path>
```

### `scripts/add-task.py`

```
Usage: python scripts/add-task.py --project-id <uuid> --title <title>

Creates a new task in ready_for_spec status.

Output:
  Created task: <title>
  Task ID: <uuid>
  Status: ready_for_spec
```

### `scripts/add-spec.py`

```
Usage: python scripts/add-spec.py --task-id <uuid> --file <spec_file_path>

Reads spec content from file, creates spec, assigns to task,
transitions task from ready_for_spec or spec_qa to ready_for_implementation.

Output:
  Created spec: <spec_id>
  Assigned to task: <task_id>
  Task status: ready_for_implementation
```

### `scripts/run-next.py`

```
Usage: python scripts/run-next.py

Picks the oldest ready_for_implementation task and executes it.
Thin wrapper around python -m worker.

Output: worker output passthrough
```

### `scripts/board.py`

```
Usage: python scripts/board.py

Prints current task board grouped by status.

Output example:
  === RATCHET BOARD ===

  READY FOR SPEC (1)
    [a1b2c3d4] Add authentication — my-project — 0 refinements

  IN PROGRESS (1)
    [e5f6g7h8] Fix login bug — my-project — 2 refinements

  BLOCKED (2)
    [i9j0k1l2] Improve test coverage — my-project — 1 refinement
    [m3n4o5p6] Add dark mode — other-project — 0 refinements

  DEPLOYED (3)
    [q7r8s9t0] Initial setup — my-project — 0 refinements
    ...
```

### `scripts/review-blocked.py`

```
Usage: python scripts/review-blocked.py

Shows all blocked tasks with failure context.

Output example:
  === BLOCKED TASKS ===

  [a1b2c3d4] Fix login bug — my-project
  Spec: <spec_id>
  Refinements: 2
  Last failure: process exited with code 1. Last output:
    AssertionError: expected 200 got 404
```

## Task Creation Event

`add-task.py` needs to append a `TASK_CREATED` event. This event type exists in `core/events.py` but no manager currently creates tasks. Add task creation directly in the script:

```python
# Append TASK_CREATED event directly via store
await store.append_event(
    aggregate_id=task_id,
    aggregate_type='task',
    event_type=EventTypes.TASK_CREATED,
    payload={
        'task_id': str(task_id),
        'project_id': str(project_id),
        'title': title,
        'status': TaskStatus.READY_FOR_SPEC,
    }
)
```

Note: Task creation logic lives in the script for now. A `TaskManager` can be extracted in a future spec when the pattern is needed elsewhere.

## `add-spec.py` Transition Logic

The task may be in `ready_for_spec` or `spec_qa` or `blocked` when a spec is added.
Valid transitions to `ready_for_implementation`:

- `ready_for_spec` → `spec_qa` → `ready_for_implementation` (two steps)
- `blocked` → `ready_for_implementation` (one step, valid per transition table)

`add-spec.py` should check current status and apply correct transition sequence:

- If `ready_for_spec`: transition to `spec_qa`, then to `ready_for_implementation`
- If `spec_qa` or `blocked` or `ready_for_implementation`: transition directly to `ready_for_implementation`
- If any other status: print error "Cannot assign spec to task in status <status>" and exit 1

## Tasks

- [ ] Write Alembic migration to drop and recreate `current_projects` view with `repo_url` and `local_path`
- [ ] Run `alembic upgrade head` and verify migration succeeds
- [ ] Run `python db/smoke_test.py` and verify still passes
- [ ] Create `scripts/add-project.py` using `ProjectManager.register_project()`
- [ ] Create `scripts/add-task.py` appending `TASK_CREATED` event directly via store
- [ ] Create `scripts/add-spec.py` using `SpecManager`, `TaskStateMachine`, handling transition logic above
- [ ] Create `scripts/run-next.py` as thin wrapper invoking worker
- [ ] Create `scripts/board.py` — replay task and project events, group by status, print formatted board
- [ ] Create `scripts/review-blocked.py` — find blocked tasks, find most recent failed execution for each, print failure reasons
- [ ] Update `CLAUDE.md` with scripts documentation and full workflow
- [ ] Run `python scripts/board.py` against real Postgres and verify clean output
- [ ] Run `python scripts/add-project.py --name ratchet --path $(pwd)` and verify project registered
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Run `pytest core/tests/ worker/tests/ -v` and verify no regressions
- [ ] Commit: `git add -A && git commit -m "spec(09): operational scripts and migration fix"`

## Assumptions

- Postgres running on 127.0.0.1:5432, DATABASE_URL set
- `$(pwd)` when running `add-project.py` is the ratchet repo root — valid onboarding target
- Scripts are run from repo root so relative imports work
- `review-blocked.py` finds failure reason from most recent `EXECUTION_FAILED` event for each blocked task
- Short UUID display (first 8 chars) is sufficient for human readability in board output
- `run-next.py` can simply call `worker.runner.main()` directly rather than subprocess

## Verification Commands

```bash
alembic upgrade head
python db/smoke_test.py
ruff check .
pytest core/tests/ worker/tests/ -v
python scripts/board.py
python scripts/add-project.py --name ratchet --path $(pwd)
```

## What Exists After This Spec

```
scripts/
  add-project.py    — register a repo as a managed project
  add-task.py       — create a task
  add-spec.py       — add and assign a spec, advance task to ready_for_implementation
  run-next.py       — execute next ready task via worker
  board.py          — display task board
  review-blocked.py — show blocked tasks with failure reasons
  run-spec.sh       — unchanged
db/
  migrations/       — new migration fixing current_projects view
CLAUDE.md           — updated with scripts documentation and full workflow
```

Ratchet is now usable end-to-end. A project can be registered, tasks created, specs assigned, worker invoked, and results observed — all from the command line. This is the first dogfooding milestone: Ratchet can now manage its own backlog.

```

After this spec, future work is tracked as tasks on the Ratchet board itself.
```
