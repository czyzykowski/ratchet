# Baseline QA Check Before Task Execution

## Objective

Before executing any implementation task, run QA steps on the base branch (`develop`) in `run_once`; if the baseline fails, skip the task and log a clear warning instead of running execution that will inevitably fail QA.

## Background

When `develop` has pre-existing lint/typecheck failures, every task execution creates a worktree from that broken base, runs implementation, then fails QA — not because of the task's own changes but because of pre-existing issues. This wastes Claude Code invocations and fills tasks with misleading failure reasons. The fix is to detect this upfront and skip execution until the base is clean.

## Success Criteria

- [ ] `check_baseline_qa(project_path)` function exists in `core/qa_runner.py`
- [ ] `check_baseline_qa` returns `[]` when QA config is absent
- [ ] `check_baseline_qa` returns `[]` when all QA steps pass
- [ ] `check_baseline_qa` returns the list of failed `QaStepResult` objects when any step fails
- [ ] `check_baseline_qa` runs QA steps in `project_path` directly (no worktree created)
- [ ] `run_once` in `worker/runner.py` calls `check_baseline_qa(project.local_path)` after selecting a task but before calling `state_machine.transition(task.id, ev.IN_PROGRESS)`
- [ ] When baseline fails, `run_once` logs a `WARNING` at level `logging.WARNING` with message containing `"Baseline QA failed"`, the project name, the task id, and the step output
- [ ] When baseline fails, `run_once` returns `False` (not `True`)
- [ ] When baseline fails, the task status is NOT changed — it stays in `ready_for_implementation`
- [ ] When baseline passes, `run_once` continues execution exactly as before
- [ ] Unit tests for `check_baseline_qa` exist in `core/tests/test_qa_runner.py`
- [ ] Unit test for the skip-on-baseline-failure path exists in `worker/tests/test_loop.py`
- [ ] `pytest core/tests/ worker/tests/ -v` passes
- [ ] `ruff check .` passes
- [ ] `mypy core/ worker/ web/` passes

## Out of Scope

- Do not change `run_qa_once` — the baseline check only applies to `run_once` (implementation execution)
- Do not create a git worktree for the baseline check — run directly in `project.local_path`
- Do not modify `notification_loop`, `compile_once`, or any other function in `worker/runner.py`
- Do not change the task state machine or add new task statuses
- Do not change `ratchet.yaml` or `QaConfig`
- Do not add caching or debouncing of baseline checks

## Technical Context

- Stack: Python 3.12, asyncio, pytest, mypy (strict), ruff
- Primary files to modify:
  - `core/qa_runner.py` — add `check_baseline_qa` function
  - `worker/runner.py` — call `check_baseline_qa` in `run_once`
- Test files to modify:
  - `core/tests/test_qa_runner.py` — unit tests for `check_baseline_qa`
  - `worker/tests/test_loop.py` — integration test for skip-on-failure path
- Existing infrastructure to reuse (do not rewrite):
  - `load_qa_config(local_path: str) -> QaConfig | None` — `core/qa_runner.py:39`
  - `run_qa_steps(config: QaConfig, cwd: str) -> list[QaStepResult]` — `core/qa_runner.py:81`
  - `QaStepResult` dataclass — `core/qa_runner.py:25`

## Pattern to Follow

### `check_baseline_qa` — place after `run_qa_steps` in `core/qa_runner.py` (after line 105)

```python
def check_baseline_qa(project_path: str) -> list[QaStepResult]:
    """Run QA steps on the base branch to detect pre-existing failures.

    Runs steps in project_path directly (no worktree). Returns list of
    failed QaStepResult objects; empty list means all passed or no QA config.
    """
    config = load_qa_config(project_path)
    if config is None:
        return []
    results = run_qa_steps(config, project_path)
    return [r for r in results if r.returncode != 0]
```

### `run_once` guard — insert between lines 179 and 180 of `worker/runner.py`

Current line 179: `task, project, spec = result`
Current line 180: `execution_manager = ExecutionManager(store, project.local_path)`

Insert after line 179:

```python
    baseline_failures = check_baseline_qa(project.local_path)
    if baseline_failures:
        combined = "\n\n".join(
            f"Step '{r.step_name}':\n{r.output}" for r in baseline_failures
        )
        logger.warning(
            "Baseline QA failed for project=%s — skipping task=%s until develop is clean.\n%s",
            project.name,
            task.id,
            combined,
        )
        return False
```

Also add the import at the top of `worker/runner.py` (it is already partially imported — verify `check_baseline_qa` is included in the import from `core.qa_runner`).

Current import line in `worker/runner.py` (around line 19):
```python
from core.qa_runner import (
    build_review_prompt,
    get_git_diff,
    load_qa_config,
    parse_review_output,
    run_qa_steps,
)
```

Add `check_baseline_qa` to this import block.

## Tasks

- [ ] **Task 1**: Add `check_baseline_qa` to `core/qa_runner.py`
  - Insert the function after `run_qa_steps` (after line 105)
  - Signature: `def check_baseline_qa(project_path: str) -> list[QaStepResult]:`
  - Body: call `load_qa_config`, return `[]` if None, else call `run_qa_steps` and filter for `returncode != 0`

- [ ] **Task 2**: Write unit tests for `check_baseline_qa` in `core/tests/test_qa_runner.py`
  - Import `check_baseline_qa` alongside existing imports
  - Test: returns `[]` when no `ratchet.yaml` exists (use `tmp_path`)
  - Test: returns `[]` when all steps pass (mock `subprocess.run` to return rc=0)
  - Test: returns failed steps when a step fails (mock `subprocess.run` to return rc=1)
  - Test: returns only the failed step when first step fails and second would have been skipped (run_qa_steps stops at first failure)
  - Follow the existing test style in `core/tests/test_qa_runner.py` — use `tmp_path`, `patch("subprocess.run", ...)`, `MagicMock`

- [ ] **Task 3**: Add `check_baseline_qa` to the import in `worker/runner.py`
  - Find the `from core.qa_runner import (...)` block (around line 19)
  - Add `check_baseline_qa` to the import list (keep alphabetical order)

- [ ] **Task 4**: Add baseline guard to `run_once` in `worker/runner.py`
  - Find line `task, project, spec = result` (line 179)
  - Insert the baseline check block immediately after (before `execution_manager = ExecutionManager(...)`)
  - Returns `False` when baseline fails, does not transition task state

- [ ] **Task 5**: Write unit test for the skip path in `worker/tests/test_loop.py`
  - Test name: `test_run_once_skips_when_baseline_qa_fails`
  - Setup: project + task in `ready_for_implementation` with spec assigned (use existing `_setup_project`, `_setup_task`, `_setup_spec` helpers)
  - Patch `worker.runner.check_baseline_qa` to return a list with one fake `QaStepResult(step_name="lint", command="ruff check .", returncode=1, output="E501 line too long")`
  - Assert: `run_once(store)` returns `False`
  - Assert: task status is still `ready_for_implementation` after the call (replay events and check)
  - Verify no Claude Code invocation was made (invoker mock not called)

- [ ] **Task 6**: Verify all QA passes
  - Run `pytest core/tests/ worker/tests/ -v` — all tests must pass
  - Run `ruff check .` — no errors
  - Run `mypy core/ worker/ web/` — no errors
  - Commit: `git add -A && git commit -m "feat: skip task execution when baseline QA fails on develop"`

## Test Requirements

Framework: pytest with `pytest-asyncio` (async tests use `@pytest.mark.anyio` or `async def` with the existing marker pattern — check existing tests in `worker/tests/test_loop.py` for the correct marker).

Scenarios for `check_baseline_qa` (in `core/tests/test_qa_runner.py`):
- No ratchet.yaml → returns `[]`
- ratchet.yaml present, all steps return rc=0 → returns `[]`
- ratchet.yaml present, first step returns rc=1 → returns `[QaStepResult(...)]` with that step
- ratchet.yaml present, first step returns rc=0, second returns rc=1 → returns `[QaStepResult(...)]` with second step

Scenario for `run_once` guard (in `worker/tests/test_loop.py`):
- Task is ready, but `check_baseline_qa` returns failures → `run_once` returns `False`, task stays in `ready_for_implementation`

## Assumptions

- `project.local_path` is the git working directory of the project (on `develop` branch at runtime)
- Running QA steps in `project.local_path` is fast enough for a pre-execution check (same commands as the full QA pipeline)
- The `ratchet.yaml` at `project.local_path/ratchet.yaml` is the authoritative QA config

## Verification Commands

```bash
pytest core/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```
