# Switch Web API Endpoints to Materialized View Queries

## Objective

Replace event-replay manager calls in `web/routes/api/tasks.py` and `web/board_builder.py` with direct queries against PostgreSQL materialized views via `web/queries.py`, expanding the views to cover all fields the endpoints need (depends_on, baseline QA failure, PR info, deploy hooks, feature backlink).

## Success Criteria

- [ ] `current_tasks` materialized view includes `depends_on` column (JSONB array of task UUID strings)
- [ ] `web/queries.py` has `get_task_detail(conn, task_id)` returning all fields needed by GET `/api/tasks/{task_id}`: task, specs, executions, qa_failure, baseline_qa_failure, pr_info, deploy_hooks, project_name, feature_id, feature_title, dependencies
- [ ] `web/queries.py` has `get_board_tasks(conn)` returning all fields needed by the board view: id, project_id, project_name, title, status, has_spec, refinement_count, updated_at, depends_on, baseline_qa_failure, required_capabilities
- [ ] GET `/api/tasks/{task_id}` uses `web/queries.py` instead of `TaskManager`, `SpecManager`, `ExecutionManager`, `ProjectManager`, `FeatureManager`
- [ ] Board endpoint uses `web/queries.py` instead of `board_builder.load_board()`
- [ ] GET `/api/tasks/{task_id}` makes at most 3 SQL queries (task detail + specs + executions) instead of 5+ event replay round trips
- [ ] Board endpoint makes 1 SQL query instead of N event replays
- [ ] `refresh_views()` is called after every `append_event()` in PostgresStore (not just periodically)
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`
- [ ] Lint passes: `ruff check .`
- [ ] Type check passes: `mypy core/ worker/ web/`
- [ ] Migration applies cleanly: `.venv/bin/alembic -c db/alembic.ini upgrade head`

## Out of Scope

- Do not change `core/` managers (`TaskManager`, `SpecManager`, `ExecutionManager`, `ProjectManager`, `FeatureManager`) — they stay event-replay based for worker/dispatch use
- Do not change `InMemoryStore` — tests are unaffected
- Do not change worker dispatch logic (`worker/task_finder.py`, `worker/pipelines/`)
- Do not change `core/store.py` Store protocol (except adding refresh after append_event in PostgresStore)
- Do not change the SPA frontend — only the JSON API response shape must remain identical
- Do not create new materialized views for baseline_qa_failure, pr_info, deploy_hooks — query the events table directly for these (they are per-task event scans, not projections across all tasks)

## Technical Context

- Stack: Python 3.12, FastAPI, PostgreSQL, psycopg (async), Alembic migrations
- Database: Postgres on 127.0.0.1:5432, database `ratchet`, user `ratchet`
- Entry point: `web/routes/api/tasks.py` — the endpoint to migrate
- Pattern to follow: `web/queries.py` — existing view query functions (lines 12-327)
- Related files:
  - `web/queries.py` (327 lines) — existing materialized view query functions: `get_task()`, `get_project()`, `get_task_specs()`, `get_task_executions()`, `get_task_dependencies()`, `get_task_qa_failure_reason()`, `get_project_tasks()`
  - `web/routes/api/tasks.py` (891 lines) — GET `/api/tasks/{task_id}` at line 31: currently uses managers with 5+ DB round trips
  - `web/board_builder.py` (91 lines) — `load_board()` function: fetches events per-task, calls `task_manager.get_task()` per task (2N queries)
  - `core/store.py` lines 188-191 — `PostgresStore.refresh_views()`: calls `refresh_all_views()` SQL function
  - `web/app.py` lines 92-98 — periodic refresh loop: `refresh_views()` every 10 seconds
  - `worker/runner.py` lines 254, 278 — worker calls `refresh_views()` after dispatch and after notifications
  - `db/migrations/versions/c1d2e3f4a5b6_add_merge_commit_sha_to_current_tasks.py` — pattern for modifying `current_tasks` view via migration

## Data Examples

**Current `current_tasks` view columns:**
```
id, project_id, title, status, current_spec_id, refinement_count, created_at, updated_at, required_capabilities, merge_commit_sha
```

**Missing column to add:**
```
depends_on  JSONB  -- e.g. '["uuid1", "uuid2"]' or '[]'
```

**GET /api/tasks/{task_id} response shape (must remain identical):**
```json
{
  "task": {"id": "...", "project_id": "...", "title": "...", "status": "...", ...},
  "project_name": "my-project",
  "specs": [{"id": "...", "content": "...", "created_at": "..."}],
  "executions": [{"id": "...", "status": "completed", "branch_name": "...", ...}],
  "dependencies": ["uuid1", "uuid2"],
  "qa_failure": "Step 'test': FAILED ...",
  "baseline_qa_failure": "Step 'build': ...",
  "pr_info": {"pr_url": "...", "pr_number": 42},
  "deploy_hooks": [{"name": "push", "command": "git push", "returncode": 0, "output": "..."}],
  "feature_id": "uuid",
  "feature_title": "My Feature"
}
```

**Fields that come from events table (not views):**
- `qa_failure`: `payload->>'failure_reason'` from latest `task.status_changed` where `to_status = 'blocked'`
- `baseline_qa_failure`: `payload->>'failure_output'` from latest `task.baseline_qa_failed` not cleared by `task.baseline_qa_retry` or `task.force_execute`
- `pr_info`: `payload` from `task.pr_created` event
- `deploy_hooks`: `payload->'steps'` from `task.merge_hooks_run` event
- `feature_id/feature_title`: reverse lookup via `current_high_level_specs` where `task_id` matches

## Tasks

### Task 1: Add `depends_on` to `current_tasks` materialized view

Create an Alembic migration that drops and recreates `current_tasks` with a new `depends_on` JSONB column. Follow the pattern in `db/migrations/versions/c1d2e3f4a5b6_add_merge_commit_sha_to_current_tasks.py`.

The `depends_on` column is derived from the latest `task.dependency_added` event per task:
```sql
latest_deps AS (
    SELECT DISTINCT ON (aggregate_id)
        aggregate_id,
        payload->'depends_on' AS depends_on
    FROM events
    WHERE aggregate_type = 'task'
      AND event_type = 'task.dependency_added'
    ORDER BY aggregate_id, sequence DESC
)
```

Join into the main SELECT: `COALESCE(ld.depends_on, '[]'::jsonb) AS depends_on`.

After migration, run `REFRESH MATERIALIZED VIEW CONCURRENTLY current_tasks` to populate.

Acceptance:
- Migration applies: `.venv/bin/alembic -c db/alembic.ini upgrade head`
- `current_tasks` has `depends_on` column
- `SELECT depends_on FROM current_tasks LIMIT 5` returns JSONB arrays
- Downgrade works: `.venv/bin/alembic -c db/alembic.ini downgrade -1`

### Task 2: Add `get_task_detail()` to `web/queries.py`

Add a new function `get_task_detail(conn, task_id)` that assembles the full task detail response using view queries and targeted event queries. It should return a dict matching the current GET `/api/tasks/{task_id}` response shape.

Implementation:
1. Query `current_tasks` for the task (1 query) — now includes `depends_on`
2. Query `current_projects` for `project_name` using `project_id` from step 1 (1 query)
3. Query `current_specs` for spec lineage using `task_id` (1 query)
4. Query `current_executions` for execution history using `task_id` (1 query)
5. Query events table for `qa_failure` — reuse existing `get_task_qa_failure_reason()` (1 query)
6. Query events table for `baseline_qa_failure` — new query scanning `task.baseline_qa_failed`, `task.baseline_qa_retry`, `task.force_execute` events (1 query)
7. Query events table for `pr_info` and `deploy_hooks` — scan `task.pr_created` and `task.merge_hooks_run` events (1 query, combined)
8. Query `current_high_level_specs` for feature backlink — `WHERE task_id = %s` (1 query)

Return `None` if task not found. Otherwise return dict with keys: `task`, `project_name`, `specs`, `executions`, `dependencies`, `qa_failure`, `baseline_qa_failure`, `pr_info`, `deploy_hooks`, `feature_id`, `feature_title`.

Add helper functions as needed:
- `get_task_baseline_qa_failure(conn, task_id) -> str | None`
- `get_task_pr_and_deploy_info(conn, task_id) -> tuple[dict | None, list | None]`
- `get_task_feature_backlink(conn, task_id) -> tuple[str | None, str | None]`

Acceptance:
- `get_task_detail()` exists in `web/queries.py`
- Response dict matches the exact shape of current endpoint output
- No event-replay manager imports in the new function

### Task 3: Switch GET `/api/tasks/{task_id}` to use `get_task_detail()`

Replace the body of the `get_task()` endpoint handler at `web/routes/api/tasks.py` line 31 with a call to `queries.get_task_detail()`.

**Before** (lines 32-144): creates `TaskManager`, `SpecManager`, `ExecutionManager`, `ProjectManager`, `FeatureManager`, makes 5+ separate calls.

**After**:
```python
@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        detail = await queries.get_task_detail(conn, task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return JSONResponse(detail)
```

Ensure the pool is available on `app.state.pool` (check `web/app.py` lifespan — it should already be set).

Update existing tests in `web/tests/` that test this endpoint to ensure they still pass. Tests that mock managers may need adjustment.

Acceptance:
- GET `/api/tasks/{task_id}` returns identical JSON response as before
- Endpoint no longer imports or constructs `TaskManager`, `SpecManager`, `ExecutionManager`, `ProjectManager`, `FeatureManager` (for this handler only — other handlers in the same file may still use them)
- All web tests pass: `.venv/bin/python -m pytest web/tests/ -v`

### Task 4: Add `get_board_tasks()` to `web/queries.py` and switch board endpoint

Add `get_board_tasks(conn) -> list[dict]` that returns all non-terminal tasks with their project names in a single query joining `current_tasks` and `current_projects`:

```sql
SELECT t.id, t.project_id, p.name AS project_name, t.title, t.status,
       t.current_spec_id IS NOT NULL AS has_spec, t.refinement_count,
       t.updated_at, t.depends_on, t.required_capabilities
FROM current_tasks t
JOIN current_projects p ON p.id = t.project_id
WHERE t.status NOT IN ('merged', 'abandoned')
ORDER BY t.created_at ASC
```

Update the board endpoint (in `web/routes/api/tasks.py` or wherever it lives) to use `get_board_tasks()` instead of `board_builder.load_board()`.

Acceptance:
- `get_board_tasks()` exists in `web/queries.py`
- Board endpoint returns same data shape as before
- Board endpoint makes 1 SQL query instead of N event replays
- All web tests pass

### Task 5: Refresh views after every `append_event()` in PostgresStore

Add a `refresh_views()` call after every `append_event()` in `PostgresStore` to ensure views are always fresh when the web process reads them.

Implementation in `core/store.py` PostgresStore.append_event():
```python
async def append_event(self, ...):
    # ... existing insert logic ...
    event = ...
    await self.refresh_views()
    return event
```

This ensures that any state change (task status transition, spec assignment, etc.) is immediately visible in the materialized views. The periodic 10-second refresh in `web/app.py` becomes a fallback safety net rather than the primary refresh mechanism.

Consider performance: `REFRESH MATERIALIZED VIEW CONCURRENTLY` uses the unique index and is non-blocking. If refresh takes too long, it can be debounced — but start with per-event refresh and measure.

Acceptance:
- After `append_event()`, the materialized views reflect the new event
- Existing periodic refresh continues to work as a fallback
- All tests pass (InMemoryStore.refresh_views is a no-op, unaffected)
- Worker dispatch cycle still functions correctly

### Task 6: Clean up and run full validation

- Remove unused manager imports from migrated endpoint handlers
- Verify `board_builder.py` is still needed (may have other callers) — if not, mark as deprecated
- Update CHANGELOG.md

Run full validation:
```bash
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```

Acceptance:
- All tests pass (zero failures)
- `ruff check .` passes (zero lint errors)
- `mypy core/ worker/ web/` passes (zero type errors)
- CHANGELOG updated

## Test Requirements

- Test framework: pytest with pytest-asyncio
- Existing web tests in `web/tests/` are the safety net — they verify endpoint response shapes
- New tests for `web/queries.py` functions should use the integration test pattern from `db/smoke_test.py`:
  - Insert events into the events table
  - Call `refresh_all_views()`
  - Query via the new functions
  - Assert correct output
- Test scenarios:
  - Task with no specs, no executions, no QA failure — returns empty arrays and nulls
  - Task with specs, executions, QA failure, baseline QA failure, PR info, deploy hooks — all fields populated
  - Task with depends_on — dependencies array populated
  - Board query — returns all non-terminal tasks with project names
  - Task not found — returns None

## Assumptions

- `web/queries.py` functions receive an async database connection from the pool (same pattern as existing functions)
- The pool is available at `request.app.state.pool` or accessible via `request.app.state.store`
- `REFRESH MATERIALIZED VIEW CONCURRENTLY` is fast enough for per-event refresh (< 100ms for typical event counts)
- The JSON response shape from GET `/api/tasks/{task_id}` must remain identical — the SPA frontend depends on it
- `current_high_level_specs` materialized view has a `task_id` column that links HLS entries to tasks

## Verification Commands

```bash
.venv/bin/alembic -c db/alembic.ini upgrade head
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```
