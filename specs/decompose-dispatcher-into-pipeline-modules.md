# Decompose ProjectDispatcher into Pipeline Modules

## Objective

Split the 940-line `worker/dispatcher.py` into a ~120-line `Dispatcher` facade class that delegates to three concrete pipeline classes (`ImplPipeline`, `QAPipeline`, `MergePipeline`), a shared `TaskFinder`, and two stateless helper modules (`worktree.py`, `event_helpers.py`).

## Success Criteria

- [ ] `worker/dispatcher.py` contains only `DispatchResult` and the `ProjectDispatcher` class (~120 lines); all pipeline logic removed
- [ ] `worker/worktree.py` exists with `QAWorktreeError`, `find_existing_worktree`, `safe_symlink`, `create_baseline_worktree`, `create_qa_worktree`, `remove_qa_worktree`
- [ ] `worker/event_helpers.py` exists with `has_pending_baseline_qa_failure`, `should_skip_baseline_qa`, `get_qa_fix_attempts`, `apply_execution_outcome`, `gh_command`
- [ ] `worker/task_finder.py` exists with `TaskFinder` class and a `find()` method
- [ ] `worker/pipelines/__init__.py` exists (can be empty or re-export pipeline classes)
- [ ] `worker/pipelines/impl.py` exists with `ImplPipeline` class and `async run(project_id=None) -> DispatchResult`
- [ ] `worker/pipelines/qa.py` exists with `QAPipeline` class and `async run(project_id=None) -> DispatchResult`
- [ ] `worker/pipelines/merge.py` exists with `MergePipeline` class and `async run(project_id=None) -> DispatchResult`
- [ ] `ProjectDispatcher.__init__` constructs `Managers`, `TaskFinder`, and pipeline objects internally
- [ ] `ProjectDispatcher.dispatch()` calls pipelines in order: merge -> QA -> impl (priority preserved)
- [ ] `ProjectDispatcher.dispatch_all()` dispatches per-project in parallel + compile_once (behavior preserved)
- [ ] `worker/runner.py` backwards-compat wrappers and `notification_loop` work without changes to their public interface
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`
- [ ] Lint passes: `ruff check .`
- [ ] Type check passes: `mypy core/ worker/ web/`

## Out of Scope

- Do not introduce ABC, Protocol, or base classes for pipelines -- use concrete classes only
- Do not change the `core/` module (`task_executor.py`, `execution_manager.py`, `context_assembler.py`, `invoker.py`, etc.)
- Do not change `worker/service.py`, `worker/listener.py`, or `worker/log_buffer.py`
- Do not change `worker/__main__.py`
- Do not change any web routes or scripts that import from `worker.runner`
- Do not alter dispatch priority ordering or project-level locking semantics
- Do not add new features, configuration, or extension points
- Do not change `DispatchResult` fields or semantics
- Do not delete backwards-compat wrapper functions from `runner.py` (they are used by web routes and scripts)

## Technical Context

- Stack: Python 3.12, asyncio, Pydantic models, event-sourced store
- Entry point: `worker/dispatcher.py` (940 lines) -- the file being decomposed
- Related files:
  - `worker/runner.py` -- backwards-compat wrappers (`run_once`, `run_qa_once`, `merge_once`, etc.) + `notification_loop()` that creates `ProjectDispatcher` and calls `dispatch_all()`
  - `worker/tests/test_dispatcher.py` -- existing tests (425 lines) covering `recover_orphans`, `impl_once`, `qa_once`, `merge_once`, `dispatch`
  - `core/managers.py` (40 lines) -- `Managers` facade that constructs all store-backed managers from a single `Store`
  - `core/task_executor.py` (99 lines) -- `TaskExecutor`, `ExecutionOutcome`, `ExecutionResult` used by impl pipeline
  - `core/execution_manager.py` (270 lines) -- `ExecutionManager` for worktree lifecycle + execution events
  - `core/context_assembler.py` -- `ContextAssembler`, `ExecutionContext`, `read_intent`
  - `core/invoker.py` -- `ClaudeCodeInvoker`, `InvocationResult`
  - `core/events.py` -- event type and status constants
  - `core/store.py` -- `Store` protocol, `InMemoryStore`
  - `core/qa_runner.py` -- `load_qa_config`, `run_qa_steps`, `run_auto_fixes`, `check_baseline_qa`, `build_review_prompt`, `parse_review_output`, `get_git_diff`, `load_deployment_config`, `load_merge_config`, `run_merge_steps`
  - `core/merge.py` -- `squash_merge`
  - `core/state_machine.py` -- `TaskStateMachine`
  - `core/models.py` -- `Project`, `Task`, `Spec`, `Execution`
  - `core/models_config.py` -- `WORKER_MODEL`

## Data Examples

**Current `ProjectDispatcher` public interface (preserved exactly):**
```python
class ProjectDispatcher:
    def __init__(self, store: Store, invoker: ClaudeCodeInvoker, local_capabilities: list[str] | None = None) -> None: ...
    async def recover_orphans(self) -> int: ...
    async def impl_once(self, project_id: UUID | None = None) -> DispatchResult: ...
    async def qa_once(self, project_id: UUID | None = None) -> DispatchResult: ...
    async def merge_once(self, project_id: UUID | None = None) -> DispatchResult: ...
    async def compile_once(self) -> DispatchResult: ...
    async def poll_pr_merges(self) -> None: ...
    async def dispatch(self, project_id: UUID) -> DispatchResult: ...
    async def dispatch_all(self, busy_projects: set[UUID] | None = None) -> list[DispatchResult]: ...
    async def list_active_projects(self) -> list[Project]: ...
```

**Target pipeline class interface:**
```python
class ImplPipeline:
    def __init__(self, managers: Managers, invoker: ClaudeCodeInvoker, task_finder: TaskFinder, capabilities: list[str]) -> None: ...
    async def run(self, project_id: UUID | None = None) -> DispatchResult: ...

class QAPipeline:
    def __init__(self, managers: Managers, invoker: ClaudeCodeInvoker, task_finder: TaskFinder) -> None: ...
    async def run(self, project_id: UUID | None = None) -> DispatchResult: ...

class MergePipeline:
    def __init__(self, managers: Managers, invoker: ClaudeCodeInvoker, task_finder: TaskFinder) -> None: ...
    async def run(self, project_id: UUID | None = None) -> DispatchResult: ...
```

**Target TaskFinder interface:**
```python
class TaskFinder:
    def __init__(self, managers: Managers) -> None: ...
    async def find(
        self,
        statuses: set[str],
        project_id: UUID | None = None,
        *,
        predicate: Any | None = None,
        skip_project_if_status: str | None = None,
    ) -> list[tuple[Task, Project, list[Any]]]: ...
```

**Target file layout:**
```
worker/
  dispatcher.py          # ProjectDispatcher facade (~120 lines)
  task_finder.py          # TaskFinder class (~80 lines)
  worktree.py             # Stateless worktree functions (~110 lines)
  event_helpers.py        # Stateless event query helpers (~70 lines)
  pipelines/
    __init__.py
    impl.py               # ImplPipeline (~160 lines)
    qa.py                 # QAPipeline (~155 lines)
    merge.py              # MergePipeline (~148 lines)
  runner.py               # Unchanged public interface
  service.py              # Unchanged
  listener.py             # Unchanged
  log_buffer.py           # Unchanged
```

## Tasks

### Task 1: Extract `worker/worktree.py` (stateless worktree helpers)

Move the following functions and class from `worker/dispatcher.py` lines 50-164 into `worker/worktree.py`:
- `QAWorktreeError` exception class (line 50)
- `_find_existing_worktree()` (lines 54-72) -- rename to `find_existing_worktree()`
- `_safe_symlink()` (lines 75-87) -- rename to `safe_symlink()`
- `_create_baseline_worktree()` (lines 90-116) -- rename to `create_baseline_worktree()`
- `_create_qa_worktree()` (lines 119-150) -- rename to `create_qa_worktree()`
- `_remove_qa_worktree()` (lines 153-163) -- rename to `remove_qa_worktree()`

Update `worker/dispatcher.py` to import from `worker.worktree` instead of defining these locally. All existing behavior must be preserved -- this is a pure move refactor.

Update `worker/runner.py` re-exports (lines 18-26) to import from `worker.worktree` instead of `worker.dispatcher`.

Acceptance:
- `worker/worktree.py` exists with all 6 functions/classes listed above
- `worker/dispatcher.py` no longer defines these functions -- it imports from `worker.worktree`
- `runner.py` re-exports still work (import paths updated)
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`
- Lint passes: `ruff check worker/`

### Task 2: Extract `worker/event_helpers.py` (stateless event query helpers)

Move the following functions from `worker/dispatcher.py` lines 171-243 into `worker/event_helpers.py`:
- `_has_pending_baseline_qa_failure()` (lines 171-182) -- rename to `has_pending_baseline_qa_failure()`
- `_should_skip_baseline_qa()` (lines 185-196) -- rename to `should_skip_baseline_qa()`
- `_apply_execution_outcome()` (lines 199-211) -- rename to `apply_execution_outcome()`
- `_gh_command()` (lines 214-230) -- rename to `gh_command()`
- `_get_qa_fix_attempts()` (lines 233-242) -- rename to `get_qa_fix_attempts()`

Update `worker/dispatcher.py` to import from `worker.event_helpers`. All existing behavior preserved.

Update `worker/runner.py` re-exports (lines 18-26) to import `has_pending_baseline_qa_failure` and `should_skip_baseline_qa` from `worker.event_helpers`.

Acceptance:
- `worker/event_helpers.py` exists with all 5 functions listed above
- `worker/dispatcher.py` no longer defines these functions -- it imports from `worker.event_helpers`
- `runner.py` re-exports still work
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`
- Lint passes: `ruff check worker/`

### Task 3: Extract `worker/task_finder.py` (TaskFinder class)

Create `worker/task_finder.py` with a `TaskFinder` class that encapsulates the `_find_tasks()` method currently at `worker/dispatcher.py` lines 274-336.

The `TaskFinder.__init__` takes a `Managers` instance. The `find()` method has the same signature as the current `_find_tasks()`:
```python
async def find(
    self,
    statuses: set[str],
    project_id: UUID | None = None,
    *,
    predicate: Any | None = None,
    skip_project_if_status: str | None = None,
) -> list[tuple[Task, Project, list[Any]]]: ...
```

Update `worker/dispatcher.py` to construct a `TaskFinder` in `__init__` and replace all `self._find_tasks(...)` calls with `self._task_finder.find(...)`.

Acceptance:
- `worker/task_finder.py` exists with `TaskFinder` class and `find()` method
- `worker/dispatcher.py` constructs `TaskFinder` in `__init__` and delegates to it
- No `_find_tasks` method remains in `ProjectDispatcher`
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`
- Lint passes: `ruff check worker/`

### Task 4: Extract `worker/pipelines/impl.py` (ImplPipeline)

Create `worker/pipelines/__init__.py` (empty or with re-exports).

Create `worker/pipelines/impl.py` with an `ImplPipeline` class that contains the logic currently in `ProjectDispatcher.impl_once()` (lines 364-524).

`ImplPipeline.__init__` takes: `managers: Managers`, `invoker: ClaudeCodeInvoker`, `task_finder: TaskFinder`, `capabilities: list[str]`.

`ImplPipeline.run(project_id=None) -> DispatchResult` contains the full impl_once logic:
- Task discovery via `self._task_finder.find()`
- Async secondary filters (waiting_for_input, dependency checking, spec lookup)
- Resume path for waiting_for_input tasks
- Baseline QA check (using `create_baseline_worktree`, `check_baseline_qa`, `has_pending_baseline_qa_failure`, `should_skip_baseline_qa` from helper modules)
- Fresh execution via `TaskExecutor`
- Outcome application via `apply_execution_outcome`

Update `worker/dispatcher.py` to construct `ImplPipeline` in `__init__` and have `impl_once()` delegate to `self._impl.run(project_id)`.

Acceptance:
- `worker/pipelines/__init__.py` exists
- `worker/pipelines/impl.py` exists with `ImplPipeline` class
- `ProjectDispatcher.impl_once()` is a one-line delegation to `self._impl.run()`
- All impl-related tests pass: `.venv/bin/python -m pytest worker/tests/test_dispatcher.py -v -k impl`
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`

### Task 5: Extract `worker/pipelines/qa.py` (QAPipeline)

Create `worker/pipelines/qa.py` with a `QAPipeline` class that contains the logic currently in `ProjectDispatcher.qa_once()` (lines 526-681).

`QAPipeline.__init__` takes: `managers: Managers`, `invoker: ClaudeCodeInvoker`, `task_finder: TaskFinder`.

`QAPipeline.run(project_id=None) -> DispatchResult` contains the full qa_once logic:
- Task discovery via task_finder
- QA config loading
- Execution branch lookup from events
- QA worktree creation/cleanup
- Auto-fix loop with max attempts
- Claude review invocation
- State transitions on pass/fail

Update `worker/dispatcher.py` to construct `QAPipeline` in `__init__` and have `qa_once()` delegate to `self._qa.run(project_id)`.

Acceptance:
- `worker/pipelines/qa.py` exists with `QAPipeline` class
- `ProjectDispatcher.qa_once()` is a one-line delegation to `self._qa.run()`
- All QA-related tests pass: `.venv/bin/python -m pytest worker/tests/test_dispatcher.py -v -k qa`
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`

### Task 6: Extract `worker/pipelines/merge.py` (MergePipeline)

Create `worker/pipelines/merge.py` with a `MergePipeline` class that contains the logic currently in `ProjectDispatcher.merge_once()` (lines 683-831).

`MergePipeline.__init__` takes: `managers: Managers`, `invoker: ClaudeCodeInvoker`, `task_finder: TaskFinder`.

`MergePipeline.run(project_id=None) -> DispatchResult` contains the full merge_once logic:
- Task discovery with `_no_auto_merge_failed` predicate
- Deployment mode filtering
- Execution branch and spec content lookup
- squash_merge invocation
- Deploy hooks execution
- State transitions

Also move `poll_pr_merges()` logic into this class as a separate `async poll_prs() -> None` method, since it is merge-domain logic.

Update `worker/dispatcher.py` to construct `MergePipeline` in `__init__` and have `merge_once()` delegate to `self._merge.run(project_id)` and `poll_pr_merges()` delegate to `self._merge.poll_prs()`.

Acceptance:
- `worker/pipelines/merge.py` exists with `MergePipeline` class
- `ProjectDispatcher.merge_once()` is a one-line delegation to `self._merge.run()`
- `ProjectDispatcher.poll_pr_merges()` is a one-line delegation to `self._merge.poll_prs()`
- All merge-related tests pass: `.venv/bin/python -m pytest worker/tests/test_dispatcher.py -v -k merge`
- All tests pass: `.venv/bin/python -m pytest worker/tests/ -v`

### Task 7: Clean up `worker/dispatcher.py` to facade form

After tasks 1-6, `worker/dispatcher.py` should now be a thin facade. Verify and clean:
- Remove all now-dead imports that were only needed by the extracted code
- Ensure `ProjectDispatcher.__init__` constructs: `Managers`, `TaskFinder`, `ImplPipeline`, `QAPipeline`, `MergePipeline`
- Ensure all public methods are one-line delegations except `dispatch()`, `dispatch_all()`, `recover_orphans()`, and `compile_once()` which contain coordination logic
- `dispatch()` calls `self._merge.run()` -> `self._qa.run()` -> `self._impl.run()` in priority order
- `dispatch_all()` dispatches per-project in parallel + `compile_once()`
- `recover_orphans()` stays in dispatcher (uses `_task_finder.find()` + state_machine transitions)
- `compile_once()` stays in dispatcher (not task-based -- calls `core.compiler.compile_all`)
- `list_active_projects()` stays in dispatcher (delegates to project_manager)
- File should be approximately 120-150 lines

Acceptance:
- `worker/dispatcher.py` is under 200 lines
- No pipeline implementation logic remains in the file (all in `worker/pipelines/`)
- No worktree or event helper logic remains (all in `worker/worktree.py` and `worker/event_helpers.py`)
- No `_find_tasks` method exists (moved to `TaskFinder`)
- All tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`

### Task 8: Update test imports and add module-level tests

Update `worker/tests/test_dispatcher.py`:
- Ensure all test patches target the correct new module paths (e.g., `worker.pipelines.impl.create_baseline_worktree` instead of `worker.dispatcher._create_baseline_worktree`)
- Add import smoke tests verifying the new modules import cleanly:
  - `from worker.worktree import create_baseline_worktree, QAWorktreeError`
  - `from worker.event_helpers import has_pending_baseline_qa_failure`
  - `from worker.task_finder import TaskFinder`
  - `from worker.pipelines.impl import ImplPipeline`
  - `from worker.pipelines.qa import QAPipeline`
  - `from worker.pipelines.merge import MergePipeline`

Verify backwards-compat imports from `worker.runner` still work:
- `from worker.runner import _has_pending_baseline_qa_failure`
- `from worker.runner import _create_baseline_worktree`
- `from worker.runner import QAWorktreeError`

Acceptance:
- All test patches target correct module paths
- All existing tests pass with updated imports
- Backwards-compat imports from `worker.runner` are verified
- Full test suite passes: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`

### Task 9: Update ratchet.yaml and run full validation

Add `worker/pipelines/` to the mypy targets if not already covered by `worker/` glob.

Run full validation:
```bash
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```

Fix any type errors in the new modules (missing type annotations, import issues, etc.).

Acceptance:
- All tests pass (zero failures)
- `ruff check .` passes (zero lint errors)
- `mypy core/ worker/ web/` passes (zero type errors)
- `ratchet.yaml` QA steps cover the new `worker/pipelines/` directory

## Test Requirements

- Test framework: pytest with pytest-asyncio
- All tests use `InMemoryStore` -- no database required
- Existing test patterns in `worker/tests/test_dispatcher.py` to follow:
  - `_setup_project()` helper with patched `validate_repo`
  - `_setup_task()` helper creating task with project_tasks registry
  - `_make_invoker()` helper creating mocked `ClaudeCodeInvoker`
  - `_make_dispatcher()` helper constructing `ProjectDispatcher`
  - Patch constants: `PATCH_PREPARE`, `PATCH_CLEANUP`, `PATCH_READ_INTENT`, `PATCH_BASELINE_WORKTREE`, `PATCH_REMOVE_QA_WORKTREE`
- After extraction, patch paths must point to new module locations (e.g., `worker.pipelines.impl.create_baseline_worktree`)

## Assumptions

- `ProjectDispatcher` public API (method names, signatures, return types) is preserved exactly
- `DispatchResult` dataclass is unchanged
- `runner.py` backward-compat wrappers continue to construct `ProjectDispatcher` and delegate -- no changes to their public signatures
- `notification_loop()` in `runner.py` continues to work identically
- The `Managers` facade at `core/managers.py` provides all needed manager instances
- Pipeline classes are stateless except for their constructor-injected dependencies -- they are constructed once in `ProjectDispatcher.__init__` and reused across calls

## Verification Commands

```bash
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```
