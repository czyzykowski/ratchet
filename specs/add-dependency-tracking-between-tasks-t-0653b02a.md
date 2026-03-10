# Spec: Add dependency tracking between tasks

## Objective
Allow tasks to declare one or more upstream task dependencies. The worker skips tasks whose dependencies are not yet deployed. The board annotates tasks with the titles/IDs of their unmet dependencies.

## Success Criteria
- [ ] `TASK_DEPENDENCY_ADDED = "task.dependency_added"` constant added to `core/events.py`
- [ ] `Task` model in `core/models.py` has `depends_on: list[str]` field (default `[]`)
- [ ] `_build_task_from_events` in `worker/runner.py` accumulates `depends_on` from `task.dependency_added` events
- [ ] `get_next_task` in `worker/runner.py` skips tasks with any dependency not in `deployed` status
- [ ] `scripts/add-task.py` accepts `--depends-on <uuid1>,<uuid2>` and appends `task.dependency_added` event after task creation
- [ ] `--depends-on` validates each value is a well-formed UUID; exits 1 with a clear error on invalid input
- [ ] `_build_task` in `scripts/board.py` accumulates `depends_on` list
- [ ] Board prints `  [depends on: <uuid>, ...]` annotation beneath any task row where one or more deps are not yet `deployed`
- [ ] Unit tests in `core/tests/test_dependency_tracking.py` cover: event replay populates `depends_on`; worker skips task with unmet dep; worker picks task when all deps deployed

## Out of Scope
- Cycle detection
- Removing or updating declared dependencies
- Propagating dependency state to `review-blocked.py`
- Any new database tables or migrations

## Technical Context
- Events are append-only; `aggregate_type="task"` for task-level events
- `_build_task_from_events` in `worker/runner.py` replays task events into a `Task` model
- `_build_task` in `scripts/board.py` replays task events into a plain dict
- Worker calls `store.get_events(task_id, "task")` to fetch a task's event stream — same pattern used to check a dependency's current status
- `Task` model in `core/models.py` uses Pydantic; adding an optional field with a default requires no migration
- The board fetches all task events in a loop already; dependency status checks reuse the same `store.get_events` pattern

## Tasks
- [ ] Add `TASK_DEPENDENCY_ADDED = "task.dependency_added"` to `core/events.py`
- [ ] Add `depends_on: list[str] = []` to `Task` in `core/models.py`
- [ ] Update `_build_task_from_events` in `worker/runner.py` to handle `ev.TASK_DEPENDENCY_ADDED` — extend `depends_on` list with `event.payload["depends_on"]`
- [ ] Update `get_next_task` in `worker/runner.py`: after building a candidate task, if `task.depends_on` is non-empty, fetch events for each dep UUID, replay status, skip silently if any dep is not `deployed`
- [ ] Update `scripts/add-task.py`: add `--depends-on` optional argument; parse comma-separated UUIDs; validate each; after task creation event, if `--depends-on` provided, append `task.dependency_added` event with payload `{"depends_on": [str, ...]}`
- [ ] Update `_build_task` in `scripts/board.py` to handle `ev.TASK_DEPENDENCY_ADDED` — accumulate `depends_on` list in the dict
- [ ] Update board display loop: for each task with non-empty `depends_on`, fetch each dep's events and replay status; collect unmet dep UUIDs; if any unmet, print `    [depends on: <uuid1>, <uuid2>]` on the line below the task row
- [ ] Write `core/tests/test_dependency_tracking.py` with `InMemoryStore`-based tests

## Assumptions
- Full UUIDs only — no short forms accepted for `--depends-on`
- A `task.dependency_added` event lists all deps for that declaration; multiple events accumulate (union)
- Worker silently skips — no status change, no extra logging beyond debug
- Board annotation uses the raw UUID (not title) for blocked deps
- Dependency check in the board uses the same per-task event replay already happening in the loop; no additional batching

## Verification Commands
```bash
# Unit tests (no DB required)
pytest core/tests/test_dependency_tracking.py -v

# Full unit test suite must still pass
pytest core/tests/ -v

# Manual smoke: create two tasks, set dep, verify board annotation
python scripts/add-project.py --name test --path $(pwd)
# note project_id from output
python scripts/add-task.py --project-id <project_id> --title "Upstream task"
# note task_id A
python scripts/add-task.py --project-id <project_id> --title "Downstream task" --depends-on <task_id_A>
python scripts/board.py
# downstream task should show [depends on: <task_id_A>] annotation

# Verify worker skips downstream while upstream is not deployed
python -m worker
# should pick upstream (or neither if no spec), not downstream

# Invalid UUID rejected
python scripts/add-task.py --project-id <project_id> --title "Bad" --depends-on not-a-uuid
# should exit 1 with error message
```

## What Exists After This Spec
- Tasks can declare upstream dependencies via `--depends-on` at creation time
- The worker automatically respects deployment ordering by skipping tasks with unmet deps
- The board surfaces dependency annotations so operators can see what is waiting on what
- Foundation is in place for future cycle detection, dependency management scripts, or dependency-aware scheduling