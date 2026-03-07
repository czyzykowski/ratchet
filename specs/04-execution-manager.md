# Spec 04: Execution Manager and Worktree Lifecycle

## Objective

Implement the execution manager that tracks the full lifecycle of a spec execution attempt — environment preparation, execution state transitions, and cleanup — persisted as events.

## Success Criteria

- [ ] `core/execution_manager.py` implements `ExecutionManager` class accepting a `Store` instance and a `repo_path` string
- [ ] `ExecutionManager.start_execution(task_id, spec_id)` creates a git worktree, appends `EXECUTION_STARTED` event, returns an `Execution` model
- [ ] If worktree creation fails for any reason, `EXECUTION_FAILED` is appended with descriptive reason and `EnvironmentError` is raised — no `EXECUTION_STARTED` event is written
- [ ] `ExecutionManager.complete_execution(execution_id)` appends `EXECUTION_COMPLETED` event, removes worktree, returns the event
- [ ] `ExecutionManager.fail_execution(execution_id, failure_reason)` appends `EXECUTION_FAILED` event, removes worktree, returns the event
- [ ] `ExecutionManager.get_current_execution(task_id)` returns the active `Execution` (status `running`) or `None`
- [ ] `ExecutionManager.get_execution_history(task_id)` returns all executions for a task ordered by `started_at` ascending
- [ ] Worktree path follows convention: `<repo_path>/.worktrees/<execution_id>`
- [ ] Worktree cleanup is attempted even if execution state transition fails — cleanup errors are logged but do not raise
- [ ] `prepare_task_environment(repo_path, execution_id)` is implemented as a module-level function in `core/execution_manager.py`
- [ ] `cleanup_task_environment(repo_path, execution_id)` is implemented as a module-level function in `core/execution_manager.py`
- [ ] `prepare_task_environment()` runs `git worktree add <path> HEAD` — creates worktree from current HEAD
- [ ] `cleanup_task_environment()` runs `git worktree remove --force <path>`
- [ ] Unit tests in `core/tests/test_execution_manager.py` use `InMemoryStore` and mock `prepare_task_environment` and `cleanup_task_environment`
- [ ] Unit tests cover successful start → complete lifecycle
- [ ] Unit tests cover successful start → fail lifecycle
- [ ] Unit tests cover environment preparation failure — `EXECUTION_FAILED` appended, `EnvironmentError` raised, no `EXECUTION_STARTED` event written
- [ ] Unit tests cover `get_current_execution()` returning `None` when no active execution
- [ ] Unit tests cover `get_current_execution()` returning `None` after execution completes
- [ ] Unit tests cover `get_execution_history()` returning executions in correct order
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ -v` passes with no errors and no database connection required

## Out of Scope

- Do not invoke Claude Code — that is the worker's responsibility
- Do not implement the worker process
- Do not modify database schema or migrations
- Do not implement TUI or API
- Do not modify `core/store.py`, `core/state_machine.py`, or `core/spec_manager.py`
- Do not implement task status transitions — `ExecutionManager` only manages execution records
- Only create `core/execution_manager.py` and `core/tests/test_execution_manager.py`

## Technical Context

- Language: Python 3.12
- Pattern: same as `TaskStateMachine` and `SpecManager` — accepts `Store` instance, never imports concrete store
- Worktree operations use `subprocess.run()` with `check=True` — let exceptions propagate on failure
- `prepare_task_environment` and `cleanup_task_environment` are module-level functions so they can be mocked in tests
- Existing files:
  - `core/store.py` — Store protocol, InMemoryStore, PostgresStore
  - `core/events.py` — EXECUTION_STARTED, EXECUTION_COMPLETED, EXECUTION_FAILED constants
  - `core/models.py` — Execution Pydantic model
  - `core/state_machine.py` — reference pattern for Store-based modules
  - `core/tests/test_state_machine.py` — reference pattern for unit tests

## Event Payloads

### EXECUTION_STARTED

```python
{
    "execution_id": str(uuid),
    "task_id": str(task_id),
    "spec_id": str(spec_id),
    "worktree_path": str,       # absolute path to worktree
    "status": "running"
}
```

### EXECUTION_COMPLETED

```python
{
    "execution_id": str(uuid),
    "status": "completed"
}
```

### EXECUTION_FAILED

```python
{
    "execution_id": str(uuid),
    "failure_reason": str,      # human readable, describes what went wrong
    "status": "failed"
}
```

## ExecutionManager Interface

```python
class ExecutionManager:
    def __init__(self, store: Store, repo_path: str) -> None: ...

    async def start_execution(
        self,
        task_id: UUID,
        spec_id: UUID
    ) -> Execution:
        """
        Prepare environment and record execution start.
        1. Generate new execution_id
        2. Call prepare_task_environment(repo_path, execution_id)
           - If it raises, append EXECUTION_FAILED with reason and raise EnvironmentError
        3. Append EXECUTION_STARTED event
        4. Return Execution model with status 'running'
        """

    async def complete_execution(
        self,
        execution_id: UUID
    ) -> Event:
        """
        Record successful completion and clean up environment.
        1. Append EXECUTION_COMPLETED event
        2. Call cleanup_task_environment(repo_path, execution_id)
           - Log cleanup errors but do not raise
        3. Return the appended event
        """

    async def fail_execution(
        self,
        execution_id: UUID,
        failure_reason: str
    ) -> Event:
        """
        Record failure and clean up environment.
        1. Append EXECUTION_FAILED event with failure_reason
        2. Call cleanup_task_environment(repo_path, execution_id)
           - Log cleanup errors but do not raise
        3. Return the appended event
        """

    async def get_current_execution(
        self,
        task_id: UUID
    ) -> Execution | None:
        """
        Return active execution for task (status == 'running'), or None.
        Derived by replaying EXECUTION_STARTED and EXECUTION_COMPLETED/FAILED events.
        """

    async def get_execution_history(
        self,
        task_id: UUID
    ) -> list[Execution]:
        """
        Return all executions for task ordered by started_at ascending.
        """
```

## Module-Level Functions

```python
def prepare_task_environment(repo_path: str, execution_id: UUID) -> str:
    """
    Create git worktree for execution.
    Worktree path: <repo_path>/.worktrees/<execution_id>
    Runs: git worktree add <worktree_path> HEAD
    Returns worktree_path on success.
    Raises subprocess.CalledProcessError on failure.
    """

def cleanup_task_environment(repo_path: str, execution_id: UUID) -> None:
    """
    Remove git worktree for execution.
    Runs: git worktree remove --force <worktree_path>
    Logs warning on failure but does not raise.
    """
```

## Test Scenarios to Cover

```
Lifecycle — successful:
- start_execution() → EXECUTION_STARTED event appended, returns Execution with status 'running'
- start_execution() → complete_execution() → EXECUTION_COMPLETED appended
- start_execution() → fail_execution(reason) → EXECUTION_FAILED appended with correct reason
- prepare_task_environment called with correct repo_path and execution_id on start
- cleanup_task_environment called with correct args on complete
- cleanup_task_environment called with correct args on fail

Environment failure:
- prepare_task_environment raises → EXECUTION_FAILED appended, EnvironmentError raised
- prepare_task_environment raises → NO EXECUTION_STARTED event in store
- prepare_task_environment raises → failure_reason in EXECUTION_FAILED payload describes the error

Current execution:
- get_current_execution() with no executions → None
- get_current_execution() after start → returns running Execution
- get_current_execution() after complete → None
- get_current_execution() after fail → None

History:
- get_execution_history() with no executions → empty list
- get_execution_history() after two executions → ordered by started_at ascending
```

## Tasks

- [ ] Create `core/execution_manager.py` with module-level `prepare_task_environment()` and `cleanup_task_environment()`
- [ ] Implement `ExecutionManager.__init__()` accepting `store` and `repo_path`
- [ ] Implement `start_execution()` — generate UUID, call prepare, append event or fail gracefully
- [ ] Implement `complete_execution()` — append event, cleanup, log cleanup errors
- [ ] Implement `fail_execution()` — append event, cleanup, log cleanup errors
- [ ] Implement `get_current_execution()` — replay events, return running execution or None
- [ ] Implement `get_execution_history()` — replay events, return ordered list
- [ ] Create `core/tests/test_execution_manager.py` using `InMemoryStore` and `unittest.mock.patch` for worktree functions
- [ ] Cover all test scenarios above
- [ ] Run `pytest core/tests/ -v` and confirm all tests pass with no database connection
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(04): execution manager and worktree lifecycle"`

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- `git` is available in PATH
- Repo at `repo_path` is a valid git repository
- `.worktrees/` directory inside the repo is gitignored — do not add it to tracking
- `InMemoryStore` from spec 02 is the correct store for tests
- `prepare_task_environment` and `cleanup_task_environment` are mocked in all unit tests — no real git operations in tests

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
```

## What Exists After This Spec

```
core/
  execution_manager.py  — ExecutionManager, prepare/cleanup environment functions
  tests/
    test_state_machine.py     — unchanged
    test_spec_manager.py      — unchanged
    test_execution_manager.py — full unit test suite, no DB required
```

Execution lifecycle is fully implemented and tested. Worktree creation and cleanup are real git operations, mocked in tests. No Claude Code invocation yet — that is the worker's responsibility in a future spec.
