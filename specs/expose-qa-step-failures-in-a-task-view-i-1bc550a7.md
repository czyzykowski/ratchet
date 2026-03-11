# Spec N: Expose QA step failures in task view and fix execution timestamps

## Objective

Fix two bugs in the web UI:
1. `completed_at` is always NULL in execution history because neither `complete_execution()` nor `fail_execution()` writes `completed_at` into the event payload, while the `current_executions` materialized view and the execution detail route both read it from the payload.
2. When Claude Code succeeds but QA fails, the failure reason is stored in a `task.status_changed` (BLOCKED) event, not in any execution event — so the task detail page shows no failure reason for the execution.

## Success Criteria
- [ ] Execution history table on task detail page shows correct `completed_at` timestamps for completed and failed executions
- [ ] Execution detail page shows correct `completed_at` timestamp
- [ ] Task detail page shows QA failure reason when task status is `blocked` and the failure originated from a QA step
- [ ] `current_executions` materialized view `completed_at` column is populated from `occurred_at` of the terminal event
- [ ] All existing tests pass

## Out of Scope

- Per-QA-step structured data (pass/fail per step name)
- Changing how failure_reason is stored in task events
- Any other UI pages beyond task detail and execution detail

## Technical Context

**Bug 1 root cause:** `current_executions` view reads `(payload->>'completed_at')::TIMESTAMPTZ` in the `latest_status` CTE, but `complete_execution()` (`core/execution_manager.py:182`) and `fail_execution()` (`core/execution_manager.py:207`) never write `completed_at` into the payload — only `status` and optionally `failure_reason`. The execution detail route (`web/routes/executions.py:52,56`) also reads `event.payload.get("completed_at")`, which is always None.

**Bug 2 root cause:** In the QA-fails path, `worker/runner.py` calls `state_machine.transition(task_id, ev.BLOCKED, extra_payload={"failure_reason": ...})` — storing the failure reason in a `task.status_changed` event (`aggregate_type='task'`). The execution itself gets `execution.completed` with no `failure_reason`. So `current_executions.failure_reason` is NULL. The task detail template renders `exe.failure_reason` from that view, which is always NULL for QA failures. The failure reason is only visible on the `/blocked` page.

**Fix strategy:**
- Bug 1: Update `current_executions` view to use `occurred_at` from the terminal event as `completed_at`. Also fix the execution detail route to use `event.occurred_at` directly.
- Bug 2: Add `get_task_qa_failure_reason(conn, task_id)` in `web/queries.py` that queries `payload->>'failure_reason'` from the latest `task.status_changed` event where `payload->>'to_status' = 'blocked'`. Pass result to task detail template and render a "QA Failure" section.

## Tasks

- [ ] Create Alembic migration that drops and recreates `current_executions` with `occurred_at AS completed_at` in the `latest_status` CTE instead of `(payload->>'completed_at')::TIMESTAMPTZ`
- [ ] Fix `web/routes/executions.py`: change `event.payload.get("completed_at")` to `event.occurred_at` on lines 52 and 56 for both `EXECUTION_COMPLETED` and `EXECUTION_FAILED` branches
- [ ] Add `get_task_qa_failure_reason(conn: Any, task_id: UUID) -> str | None` to `web/queries.py` — queries the latest `task.status_changed` event with `to_status = 'blocked'` and returns `payload->>'failure_reason'`
- [ ] Update `web/routes/tasks.py:task_detail()` to call `get_task_qa_failure_reason` and pass `qa_failure_reason` to the template context
- [ ] Update `web/templates/tasks/detail.html` to render a "QA Failure" section above the execution history table when `qa_failure_reason` is set and task status is `blocked`
- [ ] Add unit tests in `web/tests/` covering `get_task_qa_failure_reason`: returns None when no blocked event, returns reason when blocked event has failure_reason, returns None when blocked event has no failure_reason
- [ ] Run `pytest core/tests/ web/tests/ -v` and verify all pass

## Assumptions

- The `Event` model's `occurred_at` field is always populated from the database `occurred_at` column and is reliable for use as `completed_at`
- The `task.status_changed` event payload always includes `to_status` as a string matching the status constants
- The migration can reuse the same index name `current_executions_id_idx` after dropping and recreating the view
- The downgrade for this migration should restore the previous `(payload->>'completed_at')::TIMESTAMPTZ` definition (matching the `5708622e9db5` migration's upgrade)

## Verification Commands

```bash
# Run unit tests
.venv/bin/python -m pytest core/tests/ web/tests/ -v

# Apply migration
.venv/bin/alembic -c db/alembic.ini upgrade head

# Verify completed_at is populated (requires live DB with executions)
psql $DATABASE_URL -c "SELECT id, status, started_at, completed_at FROM current_executions LIMIT 5;"

# Verify task detail page shows QA failure reason for a blocked task
# (manual check in browser)
```

## What Exists After This Spec

- Execution history on the task detail page shows correct `completed_at` timestamps
- Blocked tasks with QA failures display the failure reason directly on the task detail page, without requiring the user to navigate to the `/blocked` review page
- The execution detail page (`/executions/{id}`) shows a correct `completed_at` timestamp