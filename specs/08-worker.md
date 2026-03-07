# Spec 08: Worker — End-to-End Task Execution

## Objective

Implement the worker — a single-pass script that picks the oldest ready-for-implementation task across all projects, executes it end-to-end using all core components, and exits. This is the first time all core modules are connected together.

## Success Criteria

- [ ] `worker/runner.py` implements `run_once()` async function
- [ ] `run_once()` queries for the oldest `ready_for_implementation` task across all active projects
- [ ] `run_once()` logs "No tasks ready for implementation. Exiting." and returns if no task found
- [ ] `run_once()` transitions task to `in_progress` via `TaskStateMachine`
- [ ] `run_once()` calls `ExecutionManager.start_execution(task_id, spec_id)`
- [ ] `run_once()` calls `ContextAssembler.assemble(execution_id)`
- [ ] `run_once()` calls `ClaudeCodeInvoker.invoke(context)` — synchronous, blocks until complete
- [ ] On `InvocationResult.status == 'completed'`: calls `complete_execution()`, transitions task to `ready_for_qa`
- [ ] On `InvocationResult.status in ('failed', 'crashed')`: calls `fail_execution(failure_reason)`, transitions task to `blocked`
- [ ] On any unexpected exception during execution: calls `fail_execution()` with exception details, transitions task to `blocked`
- [ ] All state transitions and execution lifecycle calls are logged with task_id and execution_id
- [ ] `worker/runner.py` has a `main()` entry point that initializes `PostgresStore`, all managers, and calls `run_once()`
- [ ] `worker/__main__.py` exists so `python -m worker` invokes `main()`
- [ ] `worker/runner.py` implements `get_next_task()` as a module-level async function
- [ ] `get_next_task()` queries events to find oldest task in `ready_for_implementation` status with an active project
- [ ] Integration test in `worker/tests/test_runner.py` uses `InMemoryStore` and mocked `ClaudeCodeInvoker`
- [ ] Integration test covers: task found and completed successfully
- [ ] Integration test covers: task found but invocation fails — task transitions to blocked
- [ ] Integration test covers: no tasks ready — logs and returns without error
- [ ] Integration test covers: environment preparation failure — task transitions to blocked
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ worker/tests/ -v` passes with no errors
- [ ] `python -m worker` runs against real Postgres, logs "No tasks ready" and exits cleanly
- [ ] Commit: `git add -A && git commit -m "spec(08): worker end-to-end task execution"`

## Out of Scope

- Do not implement polling or daemon mode — single pass only
- Do not implement TUI or API
- Do not implement knowledge extraction
- Do not modify database schema or migrations
- Do not modify any existing core modules
- Do not implement task creation — worker only consumes existing tasks
- Only create files in `worker/`

## Technical Context

- Language: Python 3.12
- `run_once()` is async — uses `PostgresStore` which requires async context
- `ClaudeCodeInvoker.invoke()` is synchronous — run in thread via `asyncio.to_thread()`
- Global FIFO: oldest task by `created_at` across all active projects
- Task must have a current spec assigned — skip tasks with no spec and log warning
- Existing modules to import and use:
  - `core.store` — `PostgresStore`, `InMemoryStore`
  - `core.state_machine` — `TaskStateMachine`, `InvalidTransitionError`
  - `core.spec_manager` — `SpecManager`
  - `core.execution_manager` — `ExecutionManager`
  - `core.context_assembler` — `ContextAssembler`, `ContextAssemblyError`
  - `core.invoker` — `ClaudeCodeInvoker`
  - `core.project_manager` — `ProjectManager`
  - `core.events` — task status constants
  - `core.models` — `Task`, `Project`

## `get_next_task()` Logic

```python
async def get_next_task(
    store: Store,
    project_manager: ProjectManager,
    spec_manager: SpecManager,
    state_machine: TaskStateMachine
) -> tuple[Task, Project, Spec] | None:
    """
    Find oldest ready_for_implementation task with active project and assigned spec.
    Returns (task, project, spec) tuple or None if nothing ready.

    Algorithm:
    1. Get all active projects via project_manager.list_projects()
    2. For each project, replay task events to find tasks in ready_for_implementation
    3. For each candidate task, verify it has a current spec via spec_manager.get_current_spec()
    4. Skip tasks with no spec — log warning "Task <id> has no spec assigned, skipping"
    5. Collect all valid (task, project, spec) candidates
    6. Return candidate with oldest task.created_at, or None if empty
    """
```

## `run_once()` Execution Flow

```python
async def run_once(
    store: Store,
    invoker: ClaudeCodeInvoker | None = None
) -> None:
    """
    Single-pass task execution.
    invoker parameter allows injection for testing — defaults to ClaudeCodeInvoker()
    """
```

Flow:

```
1. get_next_task() → None: log "No tasks ready for implementation. Exiting." and return

2. Log: "Starting execution: task=<id> project=<name> spec=<id>"

3. TaskStateMachine.transition(task_id, 'in_progress')

4. ExecutionManager.start_execution(task_id, spec_id)
   → On EnvironmentError: fail_execution(reason), transition to 'blocked', log and return

5. ContextAssembler.assemble(execution_id)
   → On ContextAssemblyError: fail_execution(reason), transition to 'blocked', log and return

6. asyncio.to_thread(invoker.invoke, context)  ← runs synchronous invoke in thread

7. If result.status == 'completed':
   - ExecutionManager.complete_execution(execution_id)
   - TaskStateMachine.transition(task_id, 'ready_for_qa')
   - Log: "Execution completed: task=<id> trace=<path>"

8. If result.status in ('failed', 'crashed'):
   - ExecutionManager.fail_execution(execution_id, result.failure_reason)
   - TaskStateMachine.transition(task_id, 'blocked')
   - Log: "Execution failed: task=<id> reason=<failure_reason>"

9. On any unexpected exception:
   - ExecutionManager.fail_execution(execution_id, f"unexpected error: {str(e)}")
   - TaskStateMachine.transition(task_id, 'blocked')
   - Log: "Unexpected error: task=<id> error=<str(e)>"
   - Re-raise
```

## `main()` Entry Point

```python
def main() -> None:
    """
    Initialize all components with PostgresStore and run once.
    Reads DATABASE_URL from environment.
    """
    import asyncio
    asyncio.run(_main_async())

async def _main_async() -> None:
    store = PostgresStore()
    invoker = ClaudeCodeInvoker()
    await run_once(store, invoker)
```

## Integration Test Scenarios

```
No tasks ready:
- Empty store → run_once() returns without error
- Log message contains "No tasks ready"

Successful execution:
- Task in ready_for_implementation with project and spec
- Mocked invoker returns completed result
- Task transitions to ready_for_qa
- complete_execution() called with correct execution_id

Failed execution:
- Mocked invoker returns failed result with failure_reason
- Task transitions to blocked
- fail_execution() called with failure_reason

Environment failure:
- prepare_task_environment raises
- Task transitions to blocked
- fail_execution() called

Task with no spec:
- Task in ready_for_implementation but no spec assigned
- Task skipped with warning log
- run_once() returns "No tasks ready"
```

## Tasks

- [ ] Create `worker/__init__.py`
- [ ] Create `worker/tests/__init__.py`
- [ ] Create `worker/runner.py` with `get_next_task()`, `run_once()`, `main()`, `_main_async()`
- [ ] Implement `get_next_task()` following algorithm above
- [ ] Implement `run_once()` following execution flow above, using `asyncio.to_thread()` for invoker
- [ ] Implement `main()` and `_main_async()` entry points
- [ ] Create `worker/__main__.py` calling `main()`
- [ ] Create `worker/tests/test_runner.py` with integration tests using `InMemoryStore` and mocked invoker
- [ ] Ensure `prepare_task_environment` and `cleanup_task_environment` are mocked in all worker tests
- [ ] Run `pytest core/tests/ worker/tests/ -v` and confirm all tests pass
- [ ] Run `python -m worker` against real Postgres and confirm clean exit with log message
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(08): worker end-to-end task execution"`

## Assumptions

- Postgres is running on 127.0.0.1:5432, DATABASE_URL is set
- `claude` binary is in PATH for real execution — not required for tests
- Tasks always have `created_at` derivable from their `TASK_CREATED` event `occurred_at`
- A task's current status is derived by replaying events — same pattern as `get_current_status()` in TaskStateMachine
- Worker runs as a single process — no concurrency concerns in v1
- `asyncio.to_thread()` is sufficient for running synchronous invoker without blocking event loop

## Verification Commands

```bash
ruff check .
pytest core/tests/ worker/tests/ -v
python -m worker
```

## What Exists After This Spec

```
worker/
  __init__.py
  __main__.py         — enables python -m worker
  runner.py           — get_next_task, run_once, main
  tests/
    __init__.py
    test_runner.py    — integration tests, InMemoryStore, mocked invoker
```

End-to-end task execution is complete. All core components are connected for the first time. A task can be picked up, executed via Claude Code, and its outcome recorded — all driven by a single `python -m worker` invocation. V1 is functionally complete.
