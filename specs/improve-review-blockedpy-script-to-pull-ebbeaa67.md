# Spec N: Improve review-blocked.py to show QA failure reasons

## Objective

Fix `scripts/review-blocked.py` so that `_find_last_failure_reason` returns the failure reason for tasks blocked by QA failures (both tool step failures and Claude review failures), not just implementation execution failures.

QA failures store `failure_reason` in `task.status_changed` events (aggregate type `task`) via `extra_payload` when transitioning to `blocked`. The current implementation only queries `execution.failed` events, missing all QA-originated reasons.

The fix makes `task.status_changed` events the primary source for failure reasons, with `execution.failed` as a fallback for tasks where no `failure_reason` was written to the task aggregate.

Only modify scripts/review-blocked.py. Do not touch any other file.

## Success Criteria

- [ ] `_find_last_failure_reason` queries `task.status_changed` events with `to_status = 'blocked'` and `failure_reason` in payload first
- [ ] If no reason found in task events, falls back to querying `execution.failed` events (existing logic)
- [ ] The function docstring is updated to reflect the new lookup strategy
- [ ] Manual verification: a blocked task that failed QA shows its failure reason in `review-blocked.py` output

## Out of Scope

- Creating execution records for QA runs
- Distinguishing QA failure types in the display output
- Any changes to `runner.py` or how failures are recorded

## Technical Context

- `scripts/review-blocked.py:96` — `_find_last_failure_reason(store, task_id)` — raw SQL via psycopg pool
- QA tool step failure path: `runner.py` — calls `state_machine.transition(task.id, ev.BLOCKED, extra_payload={"failure_reason": combined_output, ...})`
- QA Claude review failure path: `runner.py` — calls `state_machine.transition(task.id, ev.BLOCKED, extra_payload={"failure_reason": review_result.full_output})`
- Both write to the `events` table with `aggregate_type = 'task'`, `event_type = 'task.status_changed'`, and `payload->>'to_status' = 'blocked'`
- `ev.TASK_STATUS_CHANGED = "task.status_changed"`, `ev.BLOCKED = "blocked"`
- The `sequence` column provides ordering; descending order gives the most recent event

## Tasks

- [ ] In `scripts/review-blocked.py`, rewrite `_find_last_failure_reason` to:
  1. First query `events` WHERE `aggregate_type = 'task'` AND `aggregate_id = %s` AND `event_type = 'task.status_changed'` AND `payload->>'to_status' = 'blocked'` AND `payload->>'failure_reason' IS NOT NULL` ORDER BY `sequence DESC` LIMIT 1
  2. If a row is found, return `payload->>'failure_reason'`
  3. If not found, fall back to the existing `execution.failed` subquery (unchanged)
- [ ] Update the docstring of `_find_last_failure_reason` to describe the two-stage lookup

## Assumptions

- `state_machine.transition` with `extra_payload` merges payload keys into the event payload alongside `from_status`/`to_status` — confirmed by the existing `qa_fix_attempts` key usage pattern
- The `aggregate_id` for task events is the `task_id` UUID — consistent with `store.get_events(task_id, "task")` usage throughout the codebase

## Verification Commands

```bash
# Unit test — no DB required (manual inspection sufficient, no unit test needed for a raw SQL change)
# Integration verification: find a task blocked by QA and run the script
DATABASE_URL=$DATABASE_URL .venv/bin/python scripts/review-blocked.py
```

## What Exists After This Spec

`review-blocked.py` correctly surfaces failure reasons for all blocked tasks regardless of whether they were blocked by implementation failures or QA failures (tool steps or Claude review).
