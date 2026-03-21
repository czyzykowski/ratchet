# Extract ProjectDispatcher from worker/runner.py

## Objective

Extract the dispatch lifecycle from `worker/runner.py` (1206 lines) into a `worker/dispatcher.py` module with a `ProjectDispatcher` class that owns task discovery, priority dispatch (merge > QA > impl), manager construction, and busy-project tracking — reducing `runner.py` to ~250 lines of notification loop plumbing.

## Success Criteria

- [ ] `worker/dispatcher.py` exists with `ProjectDispatcher` class and `DispatchResult` dataclass
- [ ] `ProjectDispatcher.__init__(store, invoker, local_capabilities)` constructs all managers once (ProjectManager, TaskManager, SpecManager, TaskStateMachine, ExecutionManager) instead of per-function
- [ ] Private `_find_tasks(statuses, project_id, predicate)` eliminates the project→task discovery loop duplication (currently copy-pasted 5 times across `get_next_task`, `get_next_qa_task`, `merge_once`, `poll_pr_merges`, `recover_orphaned_tasks`)
- [ ] `dispatch(project_id)` runs the merge > QA > impl priority chain for one project, returns `DispatchResult`
- [ ] `dispatch_all(busy_projects)` dispatches concurrently per non-busy project, then compiles — replaces `_dispatch_all()` closure in `notification_loop`
- [ ] `recover_orphans()` replaces the top-level `recover_orphaned_tasks()` function
- [ ] `merge_once()`, `qa_once()`, `impl_once()` are individually callable for scripts like `run-next.py` and `python -m worker`
- [ ] `worker/runner.py` retains only: `notification_loop()`, `main()`, `_main_async()`, and listener/heartbeat/PR-poll plumbing
- [ ] `worker/runner.py` imports `ProjectDispatcher` and delegates all dispatch to it
- [ ] `worker/service.py` continues to work — it calls `notification_loop()` which now uses `ProjectDispatcher` internally
- [ ] `worker/tests/test_dispatcher.py` has boundary tests for `_find_tasks`, `dispatch`, `recover_orphans`, and individual `*_once` methods
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`
- [ ] Lint passes: `ruff check .`
- [ ] Type check passes: `mypy core/ worker/ web/`

## Out of Scope

- Do not change the Store protocol or add query methods to it
- Do not change TaskManager, ProjectManager, SpecManager, or TaskStateMachine APIs
- Do not change ExecutionManager, ContextAssembler, or ClaudeCodeInvoker APIs
- Do not change the notification_loop's asyncio/Queue/LISTEN-NOTIFY architecture
- Do not change the WorkerService class in `worker/service.py`
- Do not modify `core/task_executor.py`
- Do not add protocols, abstract base classes, or plugin hooks
- Do not change the QA worktree helper functions (`_create_qa_worktree`, `_create_baseline_worktree`, `_remove_qa_worktree`, `_safe_symlink`, `_find_existing_worktree`) — move them as-is
- Do not change event types or state machine transitions
- Do not refactor the merge or QA logic itself — only move it

## Technical Context

- Stack: Python 3.12, asyncio, Pydantic models, event-sourced store, pytest + pytest-asyncio
- Entry point: `worker/runner.py` (1206 lines) — contains all dispatch functions plus notification loop
- Related files:
  - `worker/runner.py` — the file being split (all functions listed below with line numbers)
  - `worker/service.py` — `WorkerService` class that calls `notification_loop()` (line 84)
  - `worker/listener.py` — `NotificationListener` async context manager
  - `worker/tests/test_runner.py` — 702 lines of existing tests (23 test functions)
  - `core/task_executor.py` — `TaskExecutor` class (recently extracted, pattern to follow)
  - `core/store.py` — `Store` protocol (line 1), `InMemoryStore` (used in tests)
  - `core/models.py` — `Project`, `Task`, `Spec` Pydantic models
  - `core/events.py` — status constants (`IN_PROGRESS`, `READY_FOR_IMPLEMENTATION`, `READY_FOR_QA`, `READY_FOR_DEPLOYMENT`, `DEPLOYED`, `BLOCKED`, `WAITING_FOR_INPUT`)
  - `core/project_manager.py` — `ProjectManager(store)` with `list_projects()`
  - `core/task_manager.py` — `TaskManager(store)` with `get_task(task_id)`
  - `core/spec_manager.py` — `SpecManager(store)` with `get_current_spec(task_id)`
  - `core/state_machine.py` — `TaskStateMachine(store)` with `transition(task_id, new_status, extra_payload)`
  - `core/qa_runner.py` — `load_qa_config`, `run_qa_steps`, `run_auto_fixes`, `check_baseline_qa`, `load_deployment_config`, `load_merge_config`, `run_merge_steps`, `build_review_prompt`, `parse_review_output`, `get_git_diff`
  - `core/merge.py` — `squash_merge()` function
  - `core/invoker.py` — `ClaudeCodeInvoker` class
  - `core/context_assembler.py` — `ContextAssembler`, `ExecutionContext`, `read_intent`

### Functions to move from runner.py to dispatcher.py

These functions currently live in `worker/runner.py` and should move into `ProjectDispatcher`:

| Function | Lines | Becomes |
|----------|-------|---------|
| `recover_orphaned_tasks(store)` | 49–90 | `ProjectDispatcher.recover_orphans()` |
| `get_next_task(store, pm, sm, sm, caps, pid)` | 93–198 | Uses `_find_tasks()` inside `impl_once()` |
| `_has_pending_baseline_qa_failure(events)` | 201–212 | Private method or static helper |
| `_should_skip_baseline_qa(events)` | 215–226 | Private method or static helper |
| `_apply_execution_outcome(sm, task, result)` | 229–241 | Private method |
| `run_once(store, invoker, caps, pid)` | 244–360 | `ProjectDispatcher.impl_once(project_id)` |
| `get_next_qa_task(store, pm, sm, sm, pid)` | 363–409 | Uses `_find_tasks()` inside `qa_once()` |
| `_get_qa_fix_attempts(events)` | 549–558 | Private method or static helper |
| `run_qa_once(store, invoker, pid)` | 561–722 | `ProjectDispatcher.qa_once(project_id)` |
| `_gh_command(args, path)` | 725–739 | Private method or static helper |
| `poll_pr_merges(store, path)` | 742–813 | `ProjectDispatcher.poll_pr_merges()` |
| `merge_once(store, invoker, pid)` | 816–968 | `ProjectDispatcher.merge_once(project_id)` |
| `compile_once(store)` | 971–985 | `ProjectDispatcher.compile_once()` |
| `QAWorktreeError` | 412 | Move to dispatcher.py |
| `_find_existing_worktree(path, branch)` | 416–435 | Move to dispatcher.py |
| `_safe_symlink(src, dst)` | 438–455 | Move to dispatcher.py |
| `_create_baseline_worktree(path)` | 458–491 | Move to dispatcher.py |
| `_create_qa_worktree(path, branch)` | 494–533 | Move to dispatcher.py |
| `_remove_qa_worktree(path, qa_path)` | 536–546 | Move to dispatcher.py |

### Functions that stay in runner.py

| Function | Lines | Why it stays |
|----------|-------|-------------|
| `notification_loop(store, invoker, dsn, ...)` | 988–1123 | Owns asyncio lifecycle, Queue, LISTEN/NOTIFY |
| `main(watchdog_timeout, caps)` | 1126–1136 | CLI entry point |
| `_main_async(watchdog_timeout, caps)` | 1139–end | CLI async entry point |

### The duplicated discovery loop (appears 5 times)

This ~15-line pattern is repeated in `get_next_task`, `get_next_qa_task`, `merge_once`, `poll_pr_merges`, and `recover_orphaned_tasks`:

```python
active_projects = await project_manager.list_projects()
if project_id is not None:
    active_projects = [p for p in active_projects if p.id == project_id]

for project in active_projects:
    project_task_events = await store.get_events(project.id, "project_tasks")
    task_ids_seen: set[UUID] = set()
    task_ids_ordered: list[UUID] = []
    for event in project_task_events:
        tid_str = event.payload.get("task_id")
        if tid_str:
            tid = UUID(tid_str)
            if tid not in task_ids_seen:
                task_ids_seen.add(tid)
                task_ids_ordered.append(tid)
    for task_id in task_ids_ordered:
        task = await task_manager.get_task(task_id)
        if task is None or task.status != TARGET_STATUS:
            continue
        # ... function-specific logic ...
```

## Data Examples

**DispatchResult dataclass:**
```python
@dataclass(frozen=True)
class DispatchResult:
    action: str        # "merge", "qa", "impl", "compile", "idle"
    task_id: UUID | None = None
    success: bool = True
    detail: str = ""
```

**_find_tasks signature and return type:**
```python
async def _find_tasks(
    self,
    statuses: set[str],
    project_id: UUID | None = None,
    *,
    predicate: Callable[[Task, list[Event]], bool] | None = None,
    skip_project_if_status: str | None = None,
) -> list[tuple[Task, Project, list[Event]]]:
    """Find tasks across active projects matching status + optional predicate.

    Args:
        statuses: Include only tasks whose status is in this set.
        project_id: Restrict to one project. None = all active projects.
        predicate: Sync filter on (task, task_events). Called only for tasks
                   passing status check. task_events are pre-fetched.
        skip_project_if_status: Skip entire project if any task has this status.
                                Used by impl_once to skip projects with in_progress tasks.

    Returns:
        List of (task, project, task_events) sorted by task.created_at ASC.
    """
```

**Usage in impl_once (replaces get_next_task + run_once):**
```python
async def impl_once(self, project_id: UUID | None = None) -> DispatchResult:
    candidates = await self._find_tasks(
        statuses={ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT},
        project_id=project_id,
        predicate=lambda t, _: set(t.required_capabilities).issubset(set(self._capabilities)),
        skip_project_if_status=ev.IN_PROGRESS,
    )
    # async secondary filters: dependency check, pending question, spec lookup
    for task, project, task_events in candidates:
        if task.status == ev.WAITING_FOR_INPUT:
            pending = await qa_manager.get_pending_question(self._store, task.id)
            if pending is not None:
                continue
        if task.depends_on:
            if await self._has_unmet_deps(task):
                continue
        spec = await self._spec_manager.get_current_spec(task.id)
        if spec is None:
            continue
        # ... proceed with execution ...
```

**Usage in qa_once (replaces get_next_qa_task + run_qa_once):**
```python
async def qa_once(self, project_id: UUID | None = None) -> DispatchResult:
    candidates = await self._find_tasks(
        statuses={ev.READY_FOR_QA},
        project_id=project_id,
    )
    for task, project, _ in candidates:
        spec = await self._spec_manager.get_current_spec(task.id)
        if spec is None:
            continue
        # ... proceed with QA ...
```

**Usage in dispatch (replaces _dispatch_for_project):**
```python
async def dispatch(self, project_id: UUID) -> DispatchResult:
    result = await self.merge_once(project_id=project_id)
    if result.action != "idle":
        return result
    result = await self.qa_once(project_id=project_id)
    if result.action != "idle":
        return result
    return await self.impl_once(project_id=project_id)
```

**notification_loop after refactor:**
```python
async def notification_loop(store, invoker, dsn, max_workers, local_capabilities, ...):
    dispatcher = ProjectDispatcher(store, invoker, local_capabilities)
    busy_projects: set[UUID] = set()

    async def _dispatch_for_project(pid: UUID) -> None:
        try:
            await dispatcher.dispatch(pid)
        finally:
            busy_projects.discard(pid)

    async def _dispatch_all() -> None:
        projects = await dispatcher.list_active_projects()
        to_dispatch = [p.id for p in projects if p.id not in busy_projects]
        for pid in to_dispatch:
            busy_projects.add(pid)
        if to_dispatch:
            await asyncio.gather(
                *[_dispatch_for_project(pid) for pid in to_dispatch],
                return_exceptions=True,
            )
        await dispatcher.compile_once()

    async def _run_loop():
        await dispatcher.recover_orphans()
        await _dispatch_all()
        # ... same queue loop, calling _dispatch_all() on each notification ...
```

## Tasks

- [ ] **Task 1: Create `worker/dispatcher.py` with `DispatchResult` and `ProjectDispatcher` skeleton**
  - Create `DispatchResult` frozen dataclass with fields: `action: str`, `task_id: UUID | None`, `success: bool`, `detail: str`
  - Create `ProjectDispatcher` class with `__init__(self, store: Store, invoker: ClaudeCodeInvoker, local_capabilities: list[str] | None = None)`
  - Constructor creates and stores: `ProjectManager(store)`, `TaskManager(store)`, `SpecManager(store)`, `TaskStateMachine(store)` as instance attributes
  - Store `self._store`, `self._invoker`, `self._capabilities` (defaulting to `[]`)
  - Add `async def list_active_projects(self) -> list[Project]` that delegates to `self._project_manager.list_projects()`
  - Acceptance: file exists, imports cleanly, `ruff check worker/dispatcher.py` passes, `mypy worker/dispatcher.py` passes

- [ ] **Task 2: Implement `_find_tasks()` private method**
  - Signature: `async def _find_tasks(self, statuses: set[str], project_id: UUID | None = None, *, predicate: Callable[[Task, list[Any]], bool] | None = None, skip_project_if_status: str | None = None) -> list[tuple[Task, Project, list[Any]]]`
  - Lists active projects via `self._project_manager.list_projects()`, filters by `project_id` if provided
  - For each project: replays `project_tasks` events to discover task IDs (the deduplicated loop from the "duplicated discovery loop" section above)
  - For each task: calls `self._task_manager.get_task(task_id)`, skips None
  - If `skip_project_if_status` is set: scans all tasks in the project first, skips entire project if any task has that status
  - Filters by `statuses` set
  - For tasks passing status filter: fetches task events via `self._store.get_events(task_id, "task")`, applies `predicate(task, task_events)` if provided
  - Returns list of `(task, project, task_events)` sorted by `task.created_at` ascending
  - Acceptance: method works with `InMemoryStore` in isolation

- [ ] **Task 3: Move worktree helpers and static helpers to `dispatcher.py`**
  - Move these functions as-is (no logic changes): `QAWorktreeError`, `_find_existing_worktree`, `_safe_symlink`, `_create_baseline_worktree`, `_create_qa_worktree`, `_remove_qa_worktree`
  - Move these static/pure helpers as-is: `_has_pending_baseline_qa_failure`, `_should_skip_baseline_qa`, `_get_qa_fix_attempts`, `_apply_execution_outcome`, `_gh_command`
  - These should be module-level functions in `dispatcher.py`, not methods on `ProjectDispatcher`
  - Acceptance: all moved functions import correctly, no logic changes, `ruff check worker/dispatcher.py` passes

- [ ] **Task 4: Implement `recover_orphans()` method**
  - Replace the standalone `recover_orphaned_tasks(store)` function
  - Uses `self._find_tasks(statuses={ev.IN_PROGRESS})` to find orphaned tasks
  - Transitions each to `READY_FOR_IMPLEMENTATION` with payload `{"reason": "worker restart: orphan recovery"}`
  - Logs same warnings/info as the current function
  - Returns count of reset tasks
  - Acceptance: same behavior as current `recover_orphaned_tasks`, verified by existing test `test_recover_orphaned_tasks_resets_in_progress_to_ready`

- [ ] **Task 5: Implement `impl_once()` method**
  - Replaces `get_next_task()` + `run_once()` as a single method
  - Uses `self._find_tasks(statuses={ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT}, project_id=project_id, predicate=<capabilities_check>, skip_project_if_status=ev.IN_PROGRESS)`
  - Applies async secondary filters in a post-filter loop: dependency check, pending question check, spec lookup (same logic as current `get_next_task` lines 146–192)
  - Handles waiting_for_input resume path (current `run_once` lines 273–292)
  - Handles baseline QA check (current `run_once` lines 294–336)
  - Uses `TaskExecutor` for execution (current `run_once` lines 337–360)
  - Uses `_apply_execution_outcome()` for state transitions
  - Returns `DispatchResult(action="impl", task_id=task.id)` on execution, `DispatchResult(action="idle")` when no task found
  - Acceptance: same behavior as current `run_once` + `get_next_task` combined

- [ ] **Task 6: Implement `qa_once()` method**
  - Replaces `get_next_qa_task()` + `run_qa_once()` as a single method
  - Uses `self._find_tasks(statuses={ev.READY_FOR_QA}, project_id=project_id)` for discovery
  - Applies spec lookup in post-filter loop
  - Contains the full QA pipeline: config loading, execution branch lookup, worktree creation, auto-fix loop, Claude review (current `run_qa_once` lines 561–722)
  - Returns `DispatchResult(action="qa", task_id=task.id)` on execution, `DispatchResult(action="idle")` when no task found
  - Acceptance: same behavior as current `run_qa_once` + `get_next_qa_task`

- [ ] **Task 7: Implement `merge_once()` method**
  - Replaces the standalone `merge_once()` function
  - Uses `self._find_tasks(statuses={ev.READY_FOR_DEPLOYMENT}, project_id=project_id, predicate=<no_auto_merge_failed>)` for discovery
  - Applies deployment mode filter and execution branch lookup in post-filter loop (current `merge_once` lines 842–898)
  - Contains the full merge pipeline: squash_merge, merge hooks, transition to DEPLOYED (current `merge_once` lines 909–968)
  - Returns `DispatchResult(action="merge", task_id=task.id)` on merge, `DispatchResult(action="idle")` when no candidate
  - Acceptance: same behavior as current `merge_once`

- [ ] **Task 8: Implement `compile_once()` and `poll_pr_merges()` methods**
  - `compile_once()`: delegates to `core.compiler.compile_all(self._store)` via lazy import, returns `DispatchResult(action="compile")`
  - `poll_pr_merges()`: uses `self._find_tasks(statuses={ev.READY_FOR_DEPLOYMENT})` for discovery, applies PR event check in post-filter, polls GitHub via `_gh_command`, transitions merged PRs to deployed
  - Acceptance: same behavior as current standalone functions

- [ ] **Task 9: Implement `dispatch()` and `dispatch_all()` methods**
  - `dispatch(project_id)`: runs `merge_once(project_id)` → `qa_once(project_id)` → `impl_once(project_id)` priority chain, returns first non-idle result or idle
  - `dispatch_all(busy_projects)`: lists active projects, filters out busy ones, calls `dispatch()` per project concurrently via `asyncio.gather`, then calls `compile_once()`, returns list of results
  - Acceptance: `dispatch` returns the result of the first non-idle action; `dispatch_all` handles concurrency correctly

- [ ] **Task 10: Refactor `runner.py` to use `ProjectDispatcher`**
  - Remove all moved functions from `runner.py`
  - Remove imports that are no longer needed in `runner.py` (ProjectManager, TaskManager, SpecManager, TaskStateMachine, etc.)
  - `notification_loop()` creates a `ProjectDispatcher` instance and uses `dispatcher.dispatch()` and `dispatcher.dispatch_all()` instead of calling standalone functions
  - Keep `busy_projects` set management in `notification_loop` (it is infrastructure concern — tracks concurrent asyncio tasks)
  - `_main_async()` creates a `ProjectDispatcher` and calls `dispatcher.dispatch_all(set())` for single-pass mode
  - `main()` stays unchanged (it just calls `_main_async`)
  - `runner.py` should be ~200-250 lines after this refactor
  - Acceptance: `runner.py` contains only `notification_loop`, `main`, `_main_async`, and their helpers; no dispatch logic

- [ ] **Task 11: Update test file references**
  - Create `worker/tests/test_dispatcher.py` with boundary tests:
    - Test `_find_tasks` with various status filters and predicates using `InMemoryStore`
    - Test `_find_tasks` with `skip_project_if_status` correctly skips projects
    - Test `recover_orphans` resets in_progress tasks (port from existing `test_recover_orphaned_tasks_*`)
    - Test `impl_once` finds task, executes, returns DispatchResult (port from existing `test_successful_execution_*`)
    - Test `impl_once` skips tasks with unmatched capabilities (port from existing `test_task_with_unmatched_capabilities_*`)
    - Test `impl_once` skips tasks with no spec (port from existing `test_task_with_no_spec_*`)
    - Test `dispatch` priority chain: merge tried first, then QA, then impl
    - Test `dispatch` returns idle when no tasks available
  - Update `worker/tests/test_runner.py`:
    - Remove tests for functions that moved to dispatcher (recover_orphaned_tasks, get_next_task, etc.)
    - Keep any tests that specifically test notification_loop behavior if they exist
    - Update imports: `from worker.dispatcher import ProjectDispatcher` where needed
  - Acceptance: all tests pass, no tests reference moved functions via old import paths

- [ ] **Task 12: Update patch targets in existing tests**
  - Any test that patches `worker.runner._create_baseline_worktree` must be updated to `worker.dispatcher._create_baseline_worktree`
  - Any test that patches `worker.runner._remove_qa_worktree` must be updated to `worker.dispatcher._remove_qa_worktree`
  - Search for all `PATCH_*` constants in test files and update paths
  - Acceptance: `grep -r "worker.runner._" worker/tests/` returns no results for moved functions

- [ ] **Task 13: Run full validation**
  - Run `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v` — all tests pass
  - Run `ruff check .` — no lint errors
  - Run `mypy core/ worker/ web/` — no type errors
  - Run `.venv/bin/python -m worker --help` or verify `python -m worker` still works as CLI entry point
  - Acceptance: all four commands succeed with zero errors

## Test Requirements

- Test framework: pytest with pytest-asyncio
- All tests use `InMemoryStore` — no database required
- Reuse existing test helpers from `test_runner.py`: `_setup_project`, `_setup_task`, `_advance_task_to_ready`, `_setup_spec`, `_make_invoker`
  - Extract these helpers into `worker/tests/conftest.py` or import them from `test_runner.py` to avoid duplication
- Mock `ClaudeCodeInvoker.invoke` using `MagicMock` returning canned `InvocationResult` (existing pattern in `test_runner.py` line 99)
- For `_find_tasks` tests: seed the store with tasks in various statuses, verify correct filtering and ordering
- For `dispatch` priority test: mock `merge_once`, `qa_once`, `impl_once` to control which returns idle vs. action

## Assumptions

- All existing function signatures and behavior are preserved — this is a pure structural refactor (move + deduplicate)
- `ProjectDispatcher` is instantiated once per `notification_loop` invocation (long-lived within a worker process)
- Managers (ProjectManager, TaskManager, etc.) are stateless wrappers around Store — safe to construct once and reuse
- `_find_tasks()` loads task events only when a predicate is provided — if predicate is None, task_events in the returned tuple will be an empty list (callers that need events fetch them separately)
- The `busy_projects` set stays in `notification_loop` because it tracks asyncio task lifecycle, not dispatch logic
- `worker/__main__.py` calls `runner.main()` — this import path must continue to work

## Verification Commands

```bash
.venv/bin/python -m pytest worker/tests/test_dispatcher.py -v
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
.venv/bin/python -m worker
```
