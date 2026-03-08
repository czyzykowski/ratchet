# Spec: Review and fix all materialized views for model consistency

## Objective

Audit all four materialized views (`current_projects`, `current_tasks`, `current_specs`, `current_executions`) against the Pydantic models in `core/models.py` and the event payloads that populate them. Fix two confirmed discrepancies via a new Alembic migration: `current_projects` must derive `updated_at` from the most recent event across all project events (not just the creation event), and `current_executions` must expose `branch_name` from the `execution.started` payload to match the `Execution` Pydantic model.

## Success Criteria

- [ ] `current_projects` computes `updated_at` as `MAX(occurred_at)` across all events where `aggregate_type = 'project'` and `aggregate_id` matches
- [ ] `current_executions` includes a `branch_name` column extracted from `payload->>'branch_name'` for `execution.started` events
- [ ] All four views have column sets that exactly match their corresponding Pydantic model fields (`Project`, `Task`, `Spec`, `Execution`)
- [ ] New Alembic migration applies cleanly with `alembic upgrade head`
- [ ] New Alembic migration downgrades cleanly with `alembic downgrade -1`
- [ ] `db/smoke_test.py` passes after migration
- [ ] Unit tests in `core/tests/` pass
- [ ] A new test or assertion documents the expected column set for each view (prevents future drift)

## Out of Scope

- Changing how `core/store.py` or the worker reads state (both use event replay, not views)
- Adding query methods to `PostgresStore` that SELECT from views
- Changing `current_tasks` or `current_specs` — both are consistent with their models
- Adding new event types or changing existing event payload schemas

## Technical Context

**Migration chain** (newest last):
1. `c67742c064f5` — creates events table and all four views
2. `21beadfdc614` — adds `refresh_all_views()` SECURITY DEFINER function
3. `a3f8b2e1d9c0` — rewrites `current_projects` to include `repo_url`, `local_path`; this is where `updated_at = occurred_at` was locked to creation time
4. `b4e9c1f2a7d3` — adds indexes on `aggregate_id` and `aggregate_type`

**Discrepancy 1 — `current_projects.updated_at`:**
Migration `a3f8b2e1d9c0` sets `updated_at = occurred_at` from the `project.created` event only. The `Project` model has `updated_at: datetime`. Fix: restructure like `current_tasks` — use a CTE for the creation row and join a `MAX(occurred_at)` subquery over all `project` events.

**Discrepancy 2 — `current_executions.branch_name` missing:**
The `Execution` Pydantic model has `branch_name: str | None`. The `execution.started` event payload stores `branch_name` (see `execution_manager.py:146`). The view's `DISTINCT ON (aggregate_id)` selects the latest event, which may be `execution.completed` or `execution.failed` — those payloads don't carry `branch_name`. The fix is to extract `branch_name` only from the `execution.started` sub-event using a CTE, similar to how `current_tasks` tracks creation separately from status.

**`refresh_all_views()`** calls `REFRESH MATERIALIZED VIEW` on all four views. It requires no changes — the view names stay the same.

## Tasks

- [ ] Create a new Alembic migration file under `db/migrations/versions/` (run `alembic revision -m "fix_materialized_views_model_consistency"`)
- [ ] In the `upgrade()` function: DROP and recreate `current_projects` using a CTE that joins `project.created` data with `MAX(occurred_at)` across all project events for `updated_at`
- [ ] In the `upgrade()` function: DROP and recreate `current_executions` using a two-CTE approach — one CTE for `execution.started` (captures `branch_name`, `started_at`, `task_id`, `spec_id`), one CTE for the latest status event — joined to produce the final row
- [ ] In the `downgrade()` function: restore both views to their previous definitions (copy from `a3f8b2e1d9c0` for `current_projects` and `c67742c064f5` for `current_executions`)
- [ ] Write a verification SQL query (can be inline in the migration or as a comment) that lists each view's columns — confirm they match the Pydantic model fields
- [ ] Add a test in `core/tests/` (using `InMemoryStore` or a fixture) that documents the expected field set for each Pydantic model, asserting the model fields match a hard-coded list — this prevents future drift
- [ ] Run `alembic upgrade head` and confirm it applies cleanly
- [ ] Run `alembic downgrade -1` and confirm it reverses cleanly, then re-apply with `alembic upgrade head`
- [ ] Run `pytest core/tests/ -v` — all tests must pass
- [ ] Run `python db/smoke_test.py` — must pass
- [ ] Update `CHANGELOG.md` under `## [Unreleased]` — Fixed section

## Assumptions

- The `execution.started` event is always the first event for any execution aggregate — safe to use as the source of `branch_name`, `task_id`, `spec_id`, `started_at`
- No new project event types exist that should be excluded from `updated_at` computation
- `refresh_all_views()` does not need to be recreated — it refreshes by view name, which is unchanged
- The test database (`TEST_DATABASE_URL`) is available for smoke test runs

## Verification Commands

```bash
# Apply migration
alembic upgrade head

# Verify current_projects columns
psql $DATABASE_URL -c "\d current_projects"
# Expected columns: id, name, repo_url, local_path, status, created_at, updated_at

# Verify current_executions columns
psql $DATABASE_URL -c "\d current_executions"
# Expected columns: id, task_id, spec_id, status, failure_reason, branch_name, started_at, completed_at

# Verify updated_at advances beyond created_at for projects (if any events exist)
psql $DATABASE_URL -c "SELECT id, created_at, updated_at, (updated_at > created_at) AS advanced FROM current_projects LIMIT 5;"

# Unit tests
pytest core/tests/ -v

# Smoke test
python db/smoke_test.py

# Downgrade and re-upgrade
alembic downgrade -1
alembic upgrade head
```

## What Exists After This Spec

All four materialized views are structurally consistent with their Pydantic models. `current_projects.updated_at` reflects the most recent activity on a project. `current_executions.branch_name` is available for any consumer that queries the view directly. A model-field regression test exists in `core/tests/` that will catch future drift between views and models.