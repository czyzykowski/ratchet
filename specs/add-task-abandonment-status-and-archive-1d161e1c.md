# Spec 15: Add task abandonment status and archive-task script

## Objective

Add `abandoned` as a new terminal task status reachable from any existing status. Provide a `scripts/archive-task.py` script that transitions a task to `abandoned` with an optional reason and confirmation prompt. Update `scripts/board.py` to hide abandoned tasks by default and show them via `--abandoned`.

## Success Criteria
- [ ] `ABANDONED = "abandoned"` constant added to `core/events.py` and included in `TASK_STATUSES`
- [ ] `VALID_TRANSITIONS` in `core/state_machine.py` includes `abandoned` as a reachable target from every non-terminal status, plus `deployed`; `abandoned` maps to an empty set (terminal)
- [ ] `scripts/archive-task.py` accepts `--task-id` (required) and `--reason` (optional), prompts for confirmation, and appends a `TASK_STATUS_CHANGED` event with `to_status: abandoned` (and `reason` in payload if provided)
- [ ] `scripts/board.py` excludes `abandoned` tasks from default output
- [ ] `scripts/board.py --abandoned` displays only abandoned tasks under an `ABANDONED` section
- [ ] Unit tests cover: transition to `abandoned` from each valid source state; `abandoned` → anything raises `InvalidTransitionError`
- [ ] `scripts/archive-task.py --help` shows correct usage

## Out of Scope

- No database migration required — `abandoned` is stored as a string in the existing event payload, no schema change needed
- Worker and state machine cannot autonomously abandon tasks — only the script does
- No bulk-abandon operation

## Technical Context

- `core/events.py` defines all status constants and `TASK_STATUSES` tuple; add `ABANDONED` there
- `core/state_machine.py` defines `VALID_TRANSITIONS` dict and `TaskStateMachine.transition()`; the existing `transition()` method is sufficient — archive script calls it directly
- `scripts/archive-task.py` should follow the pattern of `scripts/add-task.py`: check `DATABASE_URL`, parse args, import store lazily, call `TaskStateMachine.transition()`, close pool in `finally`
- `scripts/board.py` currently iterates `STATUS_ORDER` for display; add `--abandoned` argparse flag, filter tasks whose `status == ev.ABANDONED` accordingly, add `ABANDONED` to `STATUS_LABELS`
- The `reason` field goes into the `TASK_STATUS_CHANGED` event payload alongside `from_status`, `to_status`, and `status` keys

## Tasks
- [ ] Add `ABANDONED = "abandoned"` to `core/events.py` and append to `TASK_STATUSES`
- [ ] Add `abandoned` to `VALID_TRANSITIONS` in `core/state_machine.py`: every existing status (including `deployed`) maps to `abandoned` in its allowed set; `abandoned` maps to `set()`
- [ ] Create `scripts/archive-task.py` with `--task-id` (required), `--reason` (optional), confirmation prompt (`Abandon task <id>? [y/N]:`), calls `TaskStateMachine.transition(task_id, ev.ABANDONED)` and stores reason in event payload via a direct `store.append_event` call with reason, or extend transition payload — use `store.append_event` directly to include reason alongside the standard fields
- [ ] Update `scripts/board.py`: add `--abandoned` flag; when not set, skip tasks with `status == ev.ABANDONED`; when set, show only abandoned tasks under `ABANDONED` label; add `ev.ABANDONED` to `STATUS_LABELS`
- [ ] Add unit tests in `core/tests/` covering: `abandoned` reachable from `ready_for_spec`, `ready_for_implementation`, `in_progress`, `blocked`, `deployed`; and `InvalidTransitionError` raised when transitioning out of `abandoned`

## Assumptions

- The `reason` is stored in the event payload as `{"reason": "..."}` alongside the standard status-change keys; it is not a separate event type
- Confirmation prompt reads from stdin; script exits 0 on abort (not an error)
- `--abandoned` and the default view are mutually exclusive by convention (not enforced with `argparse` mutually exclusive group)
- All scripts are run with `.venv/bin/python` per project convention

## Verification Commands
```bash
# Unit tests
pytest core/tests/ -v -k "abandon"

# Manual smoke test
.venv/bin/python scripts/archive-task.py --task-id <uuid> --reason "no longer needed"
# responds: Abandon task <uuid>? [y/N]: y  → prints "Task <uuid> abandoned."

# Board default (abandoned hidden)
.venv/bin/python scripts/board.py

# Board abandoned-only view
.venv/bin/python scripts/board.py --abandoned

# Verify help text
.venv/bin/python scripts/archive-task.py --help
```

## What Exists After This Spec

- `abandoned` is a fully integrated terminal status in the state machine
- `scripts/archive-task.py` provides a safe, confirmed path to retire unwanted tasks
- `scripts/board.py` stays uncluttered by default; archived tasks are accessible on demand via `--abandoned`
- All operational scripts listed in `CLAUDE.md` are complemented by `archive-task.py`