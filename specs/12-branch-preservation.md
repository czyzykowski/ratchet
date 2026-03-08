# Spec 12: Fix Execution Lifecycle — Named Branch and Branch Preservation

## Objective

Fix `prepare_task_environment()` to create the worktree on a named branch, and fix `cleanup_task_environment()` to preserve that branch after worktree removal. Without this fix, all code changes made during execution are lost when the worktree is cleaned up.

## Success Criteria

- [ ] `prepare_task_environment(repo_path, execution_id)` creates worktree with named branch `execution/<execution_id>`
- [ ] Worktree created via `git worktree add <path> -b execution/<execution_id> HEAD`
- [ ] `cleanup_task_environment(repo_path, execution_id)` removes worktree directory but preserves branch
- [ ] Cleanup runs `git worktree remove --force <path>` only — does NOT delete the branch
- [ ] Branch `execution/<execution_id>` still exists after cleanup — verifiable via `git branch`
- [ ] `EXECUTION_STARTED` event payload includes `branch_name` field: `execution/<execution_id>`
- [ ] `Execution` Pydantic model in `core/models.py` includes `branch_name: str | None` field
- [ ] Unit tests in `core/tests/test_execution_manager.py` updated to verify branch name in event payload
- [ ] Unit tests verify `prepare_task_environment` called with correct branch name argument
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ worker/tests/ -v` passes with no regressions
- [ ] Manual verification: run worker on a task, confirm branch `execution/<execution_id>` exists after execution
- [ ] Commit: `git add -A && git commit -m "spec(12): fix execution lifecycle branch preservation"`

## Out of Scope

- Do not implement branch merging to main — that happens at deployment stage, not here
- Do not implement branch deletion — deferred until deployment behavior is defined
- Do not modify database schema or migrations
- Do not modify TUI, API, or other scripts beyond what is necessary
- Do not implement QA stage branch checkout
- Only modify: `core/execution_manager.py`, `core/models.py`, `core/tests/test_execution_manager.py`

## Technical Context

- Language: Python 3.12
- Git worktree with named branch: `git worktree add <path> -b <branch> HEAD`
- Git worktree remove preserves branch: `git worktree remove --force <path>`
- Branch name convention: `execution/<execution_id>` — e.g. `execution/277dcc3a-8372-42a5-9558-e25bbca4ae05`
- Existing files:
  - `core/execution_manager.py` — `prepare_task_environment()`, `cleanup_task_environment()`, `ExecutionManager`
  - `core/models.py` — `Execution` Pydantic model
  - `core/tests/test_execution_manager.py` — existing tests mock prepare/cleanup functions

## Changes to `prepare_task_environment()`

```python
def prepare_task_environment(repo_path: str, execution_id: UUID) -> str:
    """
    Create git worktree on a named branch for execution.
    Worktree path: <repo_path>/.worktrees/<execution_id>
    Branch name: execution/<execution_id>
    Runs: git worktree add <worktree_path> -b execution/<execution_id> HEAD
    Returns worktree_path on success.
    Raises subprocess.CalledProcessError on failure.
    """
```

## Changes to `cleanup_task_environment()`

```python
def cleanup_task_environment(repo_path: str, execution_id: UUID) -> None:
    """
    Remove git worktree directory. Branch is preserved intentionally.
    Runs: git worktree remove --force <worktree_path>
    Does NOT delete branch execution/<execution_id>.
    Logs warning on failure but does not raise.
    """
```

## Changes to `ExecutionManager.start_execution()`

Include `branch_name` in `EXECUTION_STARTED` event payload:

```python
{
    "execution_id": str(uuid),
    "task_id": str(task_id),
    "spec_id": str(spec_id),
    "worktree_path": str,
    "branch_name": f"execution/{execution_id}",  # NEW
    "status": "running"
}
```

## Changes to `core/models.py`

Add `branch_name` field to `Execution` model:

```python
class Execution(BaseModel):
    id: UUID
    task_id: UUID
    spec_id: UUID
    status: str
    failure_reason: str | None
    branch_name: str | None    # NEW — execution/<execution_id>, None for legacy records
    started_at: datetime
    completed_at: datetime | None
```

## Test Updates Required

- Update `test_execution_manager.py` mock assertions — `prepare_task_environment` now called with same args but creates a named branch
- Add assertion that `EXECUTION_STARTED` payload contains `branch_name` field
- Add assertion that `branch_name` equals `f"execution/{execution_id}"`
- Existing mock setup for `prepare_task_environment` and `cleanup_task_environment` remains valid — mocks don't need to change, just assertions

## Tasks

- [ ] Update `prepare_task_environment()` in `core/execution_manager.py` — add `-b execution/<execution_id>` to git worktree command
- [ ] Update `cleanup_task_environment()` in `core/execution_manager.py` — confirm it only runs `git worktree remove`, does not delete branch
- [ ] Update `ExecutionManager.start_execution()` — add `branch_name` to `EXECUTION_STARTED` payload
- [ ] Update `core/models.py` — add `branch_name: str | None` to `Execution` model
- [ ] Update `core/tests/test_execution_manager.py` — add assertions for `branch_name` in payload
- [ ] Run `pytest core/tests/ worker/tests/ -v` and confirm all tests pass
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(12): fix execution lifecycle branch preservation"`

## Assumptions

- Postgres running on 127.0.0.1:5432, DATABASE_URL and TEST_DATABASE_URL set
- Git is available in PATH
- Repo at `repo_path` is a valid git repository with at least one commit
- Branch name `execution/<execution_id>` will not conflict with existing branches — execution_id is a UUID
- Legacy `Execution` records without `branch_name` are handled by `branch_name: str | None` — existing tests remain valid
- `.worktrees/` is in `.gitignore` — worktree directories are not tracked
- This spec is run via `scripts/run-spec.sh` not the worker — changes to execution pipeline cannot be self-hosted until this fix is in place

## Verification Commands

```bash
ruff check .
pytest core/tests/ worker/tests/ -v
```

## What Exists After This Spec

```
core/
  execution_manager.py  — worktree created on named branch, branch preserved after cleanup
  models.py             — Execution model includes branch_name field
  tests/
    test_execution_manager.py — updated assertions for branch_name
```

Code changes made during execution now survive worktree cleanup on the `execution/<execution_id>` branch. Tasks can produce real, persistent code changes. Branch merging to main is deferred to the deployment stage.
