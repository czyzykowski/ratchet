# Spec: Feature: automated QA agent

## Objective

Add a QA agent that automatically picks up tasks in `ready_for_qa`, runs configurable tool checks (tests, lint, typecheck) defined in `ratchet.yaml`, then invokes Claude Code for a spec-verification review. Tool failures trigger a Claude Code auto-fix loop up to a configurable retry limit. Review failures transition to `blocked` with the full review output stored. On success, task transitions to `ready_for_deployment`.

## Success Criteria

- [ ] `ratchet.yaml` exists in repo root with `qa.steps` for pytest, ruff, and mypy
- [ ] `core/qa_runner.py` parses `ratchet.yaml`, normalizes steps to `QaStep` model, runs shell commands, returns `QaStepResult` per step
- [ ] `core/qa_runner.py` assembles Claude review prompt and invokes Claude Code; detects `QA_PASSED:`/`QA_FAILED:` marker in output
- [ ] Full Claude review output (including file locations, line numbers, suggestions) is stored in the `TASK_STATUS_CHANGED` event payload under `failure_reason` when transitioning to `blocked`
- [ ] Auto-fix loop: on tool step failure, invokes `ClaudeCodeInvoker` with original spec context plus full QA failure output; re-queues task to `ready_for_qa` with incremented `qa_fix_attempts` in event payload
- [ ] `max_fix_attempts` defaults to 3; when exceeded, task transitions to `blocked`
- [ ] `VALID_TRANSITIONS` in `core/state_machine.py` includes `ready_for_qa → ready_for_qa` self-transition for auto-fix re-queue
- [ ] `worker/runner.py` has `get_next_qa_task` and `run_qa_once` functions parallel to `get_next_task`/`run_once`
- [ ] `scripts/run-qa.py` picks up next `ready_for_qa` task, runs one QA cycle, exits 1 with clear message if `DATABASE_URL` not set
- [ ] Unit tests cover `load_qa_config`, `parse_review_output`, `run_qa_steps` (mocked subprocess), and `get_next_qa_task`
- [ ] `pytest core/tests/ -v` and `pytest worker/tests/ -v` pass

## Out of Scope

- Continuous polling / daemon mode for the QA worker
- Auto-merging or deploying after QA passes
- Notifications or webhooks on QA results
- Language-specific tooling adapters beyond plain shell commands
- Git worktree isolation for QA runs

## Technical Context

- `core/events.py`: `READY_FOR_QA = "ready_for_qa"` and `READY_FOR_DEPLOYMENT = "ready_for_deployment"` already exist as constants
- `core/state_machine.py` `VALID_TRANSITIONS[READY_FOR_QA]` currently contains `{READY_FOR_DEPLOYMENT, BLOCKED, READY_FOR_SPEC, READY_FOR_IMPLEMENTATION, ABANDONED}` — the self-transition `READY_FOR_QA` must be added
- `worker/runner.py` `get_next_task` signature: `(store, project_manager, spec_manager, state_machine) -> tuple[Task, Project, Spec] | None`; iterates active projects, replays task events, finds oldest `ready_for_implementation` task with assigned spec
- `worker/runner.py` `run_once` uses `ExecutionManager` + `ContextAssembler` + `ClaudeCodeInvoker`; transitions to `READY_FOR_QA` on `COMPLETED`, `BLOCKED` on failure
- `core/invoker.py` `ClaudeCodeInvoker.invoke(context: ExecutionContext) -> InvocationResult`; runs `claude -p <prompt> --allowedTools` subprocess in worktree dir
- `worker/tests/test_runner.py` exists — new QA tests go in the same file or a new `worker/tests/test_run_qa.py`
- Git diff for Claude review: `git diff HEAD~1..HEAD` run in `project.local_path`
- `qa_fix_attempts` counter tracked in the `TASK_STATUS_CHANGED` event payload; read from latest such event before deciding whether to retry

## Tasks

- [ ] Add `ev.READY_FOR_QA` to `VALID_TRANSITIONS[ev.READY_FOR_QA]` in `core/state_machine.py`

- [ ] Create `core/qa_runner.py` with:
  - `QaStep` dataclass: `name: str`, `command: str` (plain string shorthand normalized at parse time)
  - `QaConfig` dataclass: `steps: list[QaStep]`, `max_fix_attempts: int = 3`
  - `QaStepResult` dataclass: `step_name: str`, `command: str`, `returncode: int`, `output: str` (stdout+stderr combined)
  - `QaReviewResult` dataclass: `verdict: str` (`"passed"` | `"failed"`), `full_output: str`
  - `load_qa_config(local_path: str) -> QaConfig | None`: reads `<local_path>/ratchet.yaml`, returns `None` if file absent or `qa` section missing; normalizes both plain string and `{command: ...}` step forms
  - `run_qa_steps(config: QaConfig, cwd: str) -> list[QaStepResult]`: runs each step via `subprocess.run`, captures stdout+stderr combined, stops at first failure
  - `get_git_diff(cwd: str) -> str`: runs `git diff HEAD~1..HEAD` in `cwd`, returns stdout
  - `build_review_prompt(spec_content: str, diff: str, step_results: list[QaStepResult]) -> str`: assembles prompt instructing Claude to output `QA_PASSED: <rationale>` or `QA_FAILED: <full diagnosis with file paths, line numbers, and suggested fixes>`
  - `parse_review_output(output: str) -> QaReviewResult`: scans for `QA_PASSED:` or `QA_FAILED:` marker; captures all text from marker to end of output as `full_output`

- [ ] Add `get_next_qa_task` to `worker/runner.py`: same algorithm as `get_next_task` but filters on `ev.READY_FOR_QA` status; returns `tuple[Task, Project, Spec] | None`

- [ ] Add `run_qa_once` to `worker/runner.py`:
  1. Call `get_next_qa_task`; if `None`, log "No QA tasks ready" and return
  2. Call `load_qa_config(project.local_path)`; if `None`, log "No QA config found, transitioning to ready_for_deployment" and transition task to `READY_FOR_DEPLOYMENT`, return
  3. Call `run_qa_steps(config, project.local_path)`; if any step failed:
     - Read `qa_fix_attempts` from the latest `TASK_STATUS_CHANGED` event payload for this task (default 0)
     - If `qa_fix_attempts >= config.max_fix_attempts`: transition to `BLOCKED` with payload `{"failure_reason": <combined failed step output>, "qa_fix_attempts": qa_fix_attempts}`
     - Otherwise: build auto-fix prompt (spec content + "QA tools found errors after implementation was marked complete:\n" + failed step outputs), assemble `ExecutionContext`, invoke `ClaudeCodeInvoker`, transition task to `READY_FOR_QA` with payload `{"qa_fix_attempts": qa_fix_attempts + 1}`
  4. If all steps pass: call `get_git_diff(project.local_path)`, call `build_review_prompt(spec.content, diff, step_results)`, invoke `ClaudeCodeInvoker` with review prompt
  5. Call `parse_review_output` on result:
     - `"passed"`: transition task to `READY_FOR_DEPLOYMENT`
     - `"failed"`: transition task to `BLOCKED` with payload `{"failure_reason": full_output}`

- [ ] Create `scripts/run-qa.py`: checks `DATABASE_URL` env var and exits 1 with clear message if missing; initializes store, project_manager, spec_manager, state_machine; calls `asyncio.run(run_qa_once(store, ...))` — mirrors structure of `scripts/run-next.py`

- [ ] Create `ratchet.yaml` in repo root:
  ```yaml
  qa:
    max_fix_attempts: 3
    steps:
      test: "pytest core/tests/ -v"
      lint: "ruff check ."
      typecheck: "mypy core/ worker/"
  ```

- [ ] Write unit tests in `core/tests/test_qa_runner.py`:
  - `load_qa_config` with valid yaml (plain string steps), missing file, missing `qa` section, object-form step `{command: "..."}` normalized correctly
  - `parse_review_output` with `QA_PASSED: rationale`, `QA_FAILED: multi-line diagnosis`, output with no marker (treat as failed)
  - `run_qa_steps` with mocked `subprocess.run`: all pass, first step fails (stops early), last step fails

- [ ] Write unit tests in `worker/tests/test_run_qa.py`:
  - `get_next_qa_task` returns `None` when no tasks in `ready_for_qa`
  - `get_next_qa_task` returns oldest `ready_for_qa` task when multiple exist
  - `run_qa_once` transitions to `ready_for_deployment` when `load_qa_config` returns `None`

## Assumptions

- The implementation agent commits its changes before transitioning to `ready_for_qa`, so `git diff HEAD~1..HEAD` in `project.local_path` captures the relevant diff
- `ratchet.yaml` lives at `project.local_path` root, consistent with how `CLAUDE.md` and `docs/INTENT.md` are located
- QA tool steps run directly in `project.local_path` without a worktree — implementation has already committed
- The Claude review subprocess is invoked as `subprocess.run(["claude", "-p", prompt, "--allowedTools", "Bash,Read,Glob,Grep"], cwd=project.local_path, capture_output=True)` rather than through `ExecutionContext`/`ExecutionManager`, since no worktree setup is needed
- Step object form supports only `command` field for now; additional fields (`timeout`, `env`, `cwd`) are deferred

## Verification Commands

```bash
# Unit tests
pytest core/tests/ -v
pytest worker/tests/ -v

# Verify ratchet.yaml parses correctly
python -c "import yaml; cfg = yaml.safe_load(open('ratchet.yaml')); print(cfg['qa']['steps'])"

# Verify state machine accepts ready_for_qa → ready_for_qa self-transition
python -c "
from core.state_machine import VALID_TRANSITIONS
from core import events as ev
assert ev.READY_FOR_QA in VALID_TRANSITIONS[ev.READY_FOR_QA], 'self-transition missing'
print('OK')
"

# Verify run-qa.py fails cleanly without DATABASE_URL
env -u DATABASE_URL .venv/bin/python scripts/run-qa.py; echo "exit: $?"
```

## What Exists After This Spec

A complete QA gate in the Ratchet pipeline. After implementation completes, tasks flow through `ready_for_qa` where configured tool checks (pytest, ruff, mypy for this repo) and a Claude spec-verification review run automatically. Tool failures trigger up to 3 Claude Code auto-fix attempts before blocking. Review failures store the full Claude diagnosis — including file paths, line numbers, and fix suggestions — in the event payload. `scripts/run-qa.py` provides the one-shot entry point, parallel to `scripts/run-next.py`. The end-to-end pipeline is now: `ready_for_implementation` → `in_progress` → `ready_for_qa` → `ready_for_deployment`.