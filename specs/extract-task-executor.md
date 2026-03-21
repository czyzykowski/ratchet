# Extract TaskExecutor to Collapse Execution Pipeline

## Objective

Extract the 4-module execution pipeline currently orchestrated inline in `worker/runner.py:run_once()` (lines 229–418) into a single `core/task_executor.py` module with a `TaskExecutor` class that exposes two methods: `execute()` for fresh executions and `resume()` for waiting-for-input tasks.

## Success Criteria

- [ ] `core/task_executor.py` exists with `TaskExecutor` class, `ExecutionOutcome` enum, and `ExecutionResult` dataclass
- [ ] `TaskExecutor.execute(task, spec, project)` returns `ExecutionResult` — never raises except on unexpected invoker crash
- [ ] `TaskExecutor.resume(task, project, execution_id)` returns `ExecutionResult` — skips worktree creation
- [ ] Both methods share a private `_run_invocation()` to eliminate code duplication
- [ ] Worktree cleanup always runs via `try/finally`, even when Claude crashes mid-execution
- [ ] `worker/runner.py:run_once()` uses `TaskExecutor` — pipeline plumbing reduced to ~5 lines of outcome handling
- [ ] `run_once()` still owns all `TaskStateMachine` transitions (READY_FOR_QA, BLOCKED, IN_PROGRESS)
- [ ] Baseline QA check remains in `run_once()` — it is NOT moved into `TaskExecutor`
- [ ] RATCHET_DEBUG logging remains in `run_once()` — it is caller concern
- [ ] `core/tests/test_task_executor.py` has boundary tests using `InMemoryStore` and a fake invoker
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`
- [ ] Lint passes: `.venv/bin/ruff check .`
- [ ] Type check passes: `.venv/bin/mypy core/ worker/`

## Out of Scope

- Do not change `ExecutionManager`, `ContextAssembler`, or `ClaudeCodeInvoker` APIs
- Do not move baseline QA logic into `TaskExecutor`
- Do not move QA auto-fix logic into `TaskExecutor` (it has a different worktree lifecycle)
- Do not add protocols, hooks, or extension points — keep it concrete
- Do not change the `run_qa_once()` function
- Do not change the `merge_once()` function
- Do not modify the notification loop or dispatch logic
- Do not refactor `ExecutionManager` internals

## Technical Context

- Stack: Python 3.12, asyncio, Pydantic models, event-sourced store
- Entry point: `worker/runner.py` — the `run_once()` function (line 229)
- Related files:
  - `core/execution_manager.py` — `ExecutionManager` class (line 114): creates/cleans worktrees, appends execution events
  - `core/context_assembler.py` — `ContextAssembler` class (line 221): builds Claude prompt; `ExecutionContext` dataclass (line 95)
  - `core/invoker.py` — `ClaudeCodeInvoker` class (line 149): runs `claude -p` subprocess; `InvocationResult` dataclass (line 27)
  - `core/models.py` — `Execution`, `Project`, `Task`, `Spec` Pydantic models
  - `core/store.py` — `Store` protocol, `InMemoryStore` (used in tests)
  - `core/state_machine.py` — `TaskStateMachine` (owns status transitions)
  - `core/tests/test_invoker.py` — existing invoker tests (pattern to follow for faking)
  - `worker/tests/test_runner.py` — existing runner tests (to be simplified)

## Data Examples

**ExecutionOutcome enum values:**
```python
class ExecutionOutcome(str, Enum):
    COMPLETED = "completed"   # Claude printed COMPLETED
    BLOCKED   = "blocked"     # Claude printed BLOCKED or failed
    ENV_ERROR = "env_error"   # worktree or context assembly failed before Claude ran
```

**ExecutionResult returned by execute()/resume():**
```python
ExecutionResult(
    outcome=ExecutionOutcome.COMPLETED,
    execution_id=UUID("..."),
    failure_reason=None,         # None on success
    trace_id=UUID("..."),        # None only on ENV_ERROR
)
```

**Caller usage after refactor:**
```python
result = await executor.execute(task, spec, project)
if result.outcome == ExecutionOutcome.COMPLETED:
    await state_machine.transition(task.id, ev.READY_FOR_QA)
else:
    await state_machine.transition(
        task.id, ev.BLOCKED,
        extra_payload={"failure_reason": result.failure_reason},
    )
```

## Tasks

- [ ] **Task 1: Create `core/task_executor.py` with types**
  - Create `ExecutionOutcome` enum with values `COMPLETED`, `BLOCKED`, `ENV_ERROR`
  - Create `ExecutionResult` frozen dataclass with fields: `outcome: ExecutionOutcome`, `execution_id: UUID`, `failure_reason: str | None`, `trace_id: UUID | None`
  - Create `TaskExecutor` class with `__init__(self, execution_manager: ExecutionManager, context_assembler: ContextAssembler, invoker: ClaudeCodeInvoker)`
  - Acceptance: file exists, imports cleanly, `ruff check core/task_executor.py` passes

- [ ] **Task 2: Implement `TaskExecutor._run_invocation()`**
  - Private async method that takes `task: Task`, `project: Project`, `execution_id: UUID`
  - Calls `self._context_assembler.assemble(execution_id, project)` — catches `ContextAssemblyError`, returns `ENV_ERROR` result
  - Calls `await asyncio.to_thread(self._invoker.invoke, context)` — catches `Exception`, calls `fail_execution`, re-raises
  - On `completed`: calls `self._execution_manager.complete_execution(execution_id)`
  - On `failed`/`crashed`: calls `self._execution_manager.fail_execution(execution_id, reason)`
  - Returns `ExecutionResult` mapping invoker status to `ExecutionOutcome`
  - Acceptance: method exists with proper error handling and cleanup guarantee

- [ ] **Task 3: Implement `TaskExecutor.execute()`**
  - Async method taking `task: Task`, `spec: Spec`, `project: Project`
  - Calls `self._execution_manager.start_execution(task.id, spec.id, project)` — catches `OSError`, returns `ENV_ERROR` result
  - On success, calls `self._run_invocation(task, project, execution.id)` in a `try/finally` that always calls `cleanup_task_environment`
  - Never raises except on unexpected invoker crash (same behavior as current code)
  - Acceptance: fresh execution path works end-to-end

- [ ] **Task 4: Implement `TaskExecutor.resume()`**
  - Async method taking `task: Task`, `project: Project`, `execution_id: UUID`
  - Calls `self._run_invocation(task, project, execution_id)` directly — no worktree creation
  - Does NOT call `cleanup_task_environment` (worktree may be needed for re-resume)
  - Acceptance: resume path works without creating a new worktree

- [ ] **Task 5: Write tests in `core/tests/test_task_executor.py`**
  - Create a fake invoker that returns a canned `InvocationResult` (follow pattern in `core/tests/test_invoker.py`)
  - Test `execute()` happy path: invoker returns completed → result is COMPLETED, execution marked complete
  - Test `execute()` invoker failure: invoker returns failed → result is BLOCKED, execution marked failed
  - Test `execute()` environment failure: `start_execution` raises OSError → result is ENV_ERROR
  - Test `execute()` context failure: `assemble` raises ContextAssemblyError → result is ENV_ERROR, execution marked failed
  - Test `execute()` invoker crash: invoker raises Exception → execution marked failed, exception re-raised
  - Test `resume()` happy path: no worktree creation, invoker returns completed → result is COMPLETED
  - Test `resume()` failure: invoker returns failed → result is BLOCKED
  - Use `InMemoryStore` for all tests — no database required
  - Acceptance: all tests pass with `pytest core/tests/test_task_executor.py -v`

- [ ] **Task 6: Refactor `run_once()` in `worker/runner.py` to use `TaskExecutor`**
  - After `get_next_task()` returns, instantiate `TaskExecutor(execution_manager, context_assembler, invoker)`
  - Replace the resume path (lines 258–310) with: `result = await executor.resume(task, project, execution.id)`
  - Replace the fresh execution path (lines 355–418) with: `result = await executor.execute(task, spec, project)`
  - Keep baseline QA check (lines 312–354) unchanged — it stays in `run_once()`
  - Keep RATCHET_DEBUG logging — move it to before/after the `executor.execute()` call
  - Add unified outcome handling after both paths:
    ```python
    if result.outcome == ExecutionOutcome.COMPLETED:
        await state_machine.transition(task.id, ev.READY_FOR_QA)
        logger.info("Execution completed: task=%s trace_id=%s", task.id, result.trace_id)
    else:
        failure_reason = result.failure_reason or result.outcome.value
        await state_machine.transition(
            task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
        )
        logger.info("Execution %s: task=%s reason=%s", result.outcome.value, task.id, failure_reason)
    ```
  - Acceptance: `run_once()` pipeline plumbing reduced from ~90 lines to ~30 lines

- [ ] **Task 7: Update `worker/tests/test_runner.py`**
  - Simplify tests that currently mock `ExecutionManager`, `ContextAssembler`, and `ClaudeCodeInvoker` separately — mock `TaskExecutor` instead where appropriate
  - Keep tests that test `get_next_task()`, baseline QA, and dispatch logic unchanged
  - Acceptance: all existing runner tests pass, mock count reduced

- [ ] **Task 8: Run full validation**
  - Run `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v` — all tests pass
  - Run `.venv/bin/ruff check .` — no lint errors
  - Run `.venv/bin/mypy core/ worker/` — no type errors
  - Acceptance: all three commands pass with zero errors

## Test Requirements

- Test framework: pytest with pytest-asyncio
- All tests use `InMemoryStore` — no database required
- Fake invoker pattern: create a class with `invoke()` method returning a canned `InvocationResult` and a no-op `terminate()`
- Mock `ExecutionManager` methods (`start_execution`, `complete_execution`, `fail_execution`) using `unittest.mock.AsyncMock`
- Mock `ContextAssembler.assemble` using `unittest.mock.AsyncMock`
- Test cleanup guarantee: inject invoker that raises, assert `fail_execution` was called before the exception propagates

## Assumptions

- `ExecutionManager`, `ContextAssembler`, and `ClaudeCodeInvoker` APIs do not change
- `InvocationResult.status` is always one of `"completed"`, `"failed"`, `"crashed"`
- `ExecutionManager.start_execution()` raises `OSError` on worktree creation failure
- `ContextAssembler.assemble()` raises `ContextAssemblyError` on assembly failure
- The `TaskExecutor` is instantiated per-call in `run_once()` (stateless, cheap to create)
- The shared `ClaudeCodeInvoker` instance is passed through to `TaskExecutor` — its internal `_proc_lock` handles thread safety

## Verification Commands

```bash
.venv/bin/python -m pytest core/tests/test_task_executor.py -v
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
.venv/bin/ruff check .
.venv/bin/mypy core/ worker/
```
