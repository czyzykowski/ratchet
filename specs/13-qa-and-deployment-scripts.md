# Spec 13: QA and Deployment Scripts

## Objective

Add three operational scripts for manual pipeline control: `approve-qa.py` (advance task from ready_for_qa), `deploy-task.py` (squash merge execution branch and advance to deployed), and `task-reset.py` (reset task backwards for re-execution).

## Success Criteria

### approve-qa.py

- [ ] `scripts/approve-qa.py --task-id <uuid>` advances task from `ready_for_qa` to `ready_for_deployment`
- [ ] Prints current task title and status before advancing
- [ ] Prints confirmation after: `Task advanced to ready_for_deployment`
- [ ] Exits with error if task is not in `ready_for_qa`
- [ ] Exits with error if task not found

### deploy-task.py

- [ ] `scripts/deploy-task.py --task-id <uuid>` squash merges execution branch and advances task to `deployed`
- [ ] `--branch <name>` specifies merge target, defaults to `develop`
- [ ] Reads `branch_name` from most recent `execution.started` event payload for the task
- [ ] Runs `git merge --squash execution/<execution_id>` in repo's `local_path`
- [ ] Runs `git commit -m "feat: <task title> (task/<task_id>)"` after squash merge
- [ ] Deletes execution branch after successful merge: `git branch -d execution/<execution_id>`
- [ ] Advances task status from `ready_for_deployment` to `deployed`
- [ ] Prints confirmation: `Deployed task <title> — merged to <branch>`
- [ ] Exits with error if task is not in `ready_for_deployment`
- [ ] Exits with error if `branch_name` not found in execution events
- [ ] Exits with error if git merge fails — does NOT advance task status on git failure
- [ ] Exits with error if task not found

### task-reset.py

- [ ] `scripts/task-reset.py --task-id <uuid>` resets task to `ready_for_spec`
- [ ] `--reuse-spec` flag resets task to `ready_for_implementation` using last assigned spec
- [ ] Handles reset from any non-terminal status: `spec_qa`, `ready_for_implementation`, `in_progress`, `ready_for_qa`, `ready_for_deployment`, `blocked`
- [ ] Prints current task title and status before resetting
- [ ] Prints confirmation: `Task reset to ready_for_spec` or `Task reset to ready_for_implementation`
- [ ] `--reuse-spec` exits with error if no spec has ever been assigned to the task
- [ ] Exits with error if task is in terminal status (`deployed`)
- [ ] Exits with error if task not found
- [ ] Does NOT touch git branches — branch lifecycle is separate concern

### State Machine

- [ ] `VALID_TRANSITIONS` in `core/state_machine.py` updated to allow `ready_for_qa` → `ready_for_deployment` via direct event append (already exists but doesn't work — verify and fix)
- [ ] `ready_for_deployment` → `deployed` transition works correctly
- [ ] `ready_for_qa` → `blocked` transition works correctly (already defined, verify)
- [ ] New transitions added for reset: all non-terminal states → `ready_for_spec`
- [ ] New transition added: all non-terminal states → `ready_for_implementation` (for --reuse-spec)

### General

- [ ] All scripts read `DATABASE_URL` from environment, exit 1 with clear message if not set
- [ ] All scripts use `.venv/bin/python` compatible imports — no sys.path hacks
- [ ] `ruff check .` passes
- [ ] `pytest core/tests/ worker/tests/ -v` passes with no regressions
- [ ] Manually verify: `approve-qa.py` on task `74fab081` advances it correctly
- [ ] Manually verify: `task-reset.py` on task `fb0329cd` resets it to `ready_for_spec`
- [ ] Commit: `git add -A && git commit -m "spec(13): QA and deployment scripts"`

## Out of Scope

- Do not implement automated QA — approve-qa.py is manual only
- Do not implement branch creation or worktree management
- Do not modify worker, invoker, context assembler, or execution manager
- Do not add new database migrations — state machine changes are code-only
- Do not implement task archival — that is a separate task

## Technical Context

- Language: Python 3.12
- All scripts follow pattern established in existing scripts — asyncio.run(main()), argparse, PostgresStore
- State machine transitions are validated in `core/state_machine.py` `VALID_TRANSITIONS` dict
- Execution events use `execution_id` as `aggregate_id` — to find execution branch for a task, scan `execution.started` events and filter by `payload['task_id']`
- Git operations run via `subprocess.run(..., check=True, cwd=repo_local_path)`
- Project `local_path` retrieved from `project.created` event payload for the task's project
- `branch_name` field added to `EXECUTION_STARTED` payload in spec 12 — format: `execution/<execution_id>`
- Existing scripts for reference: `scripts/add-task.py`, `scripts/add-spec.py`, `scripts/board.py`

## State Machine Changes

Current `VALID_TRANSITIONS` gaps to fix:

- `ready_for_qa` → `ready_for_deployment` — defined but not working, investigate and fix
- Reset paths missing entirely

Add reset transitions:

```python
VALID_TRANSITIONS: dict[str, set[str]] = {
    ev.READY_FOR_SPEC: {ev.SPEC_QA},
    ev.SPEC_QA: {ev.READY_FOR_IMPLEMENTATION, ev.BLOCKED, ev.READY_FOR_SPEC},
    ev.READY_FOR_IMPLEMENTATION: {ev.IN_PROGRESS, ev.BLOCKED, ev.READY_FOR_SPEC},
    ev.IN_PROGRESS: {ev.BLOCKED, ev.READY_FOR_QA, ev.READY_FOR_SPEC},
    ev.READY_FOR_QA: {ev.READY_FOR_DEPLOYMENT, ev.BLOCKED, ev.READY_FOR_SPEC, ev.READY_FOR_IMPLEMENTATION},
    ev.READY_FOR_DEPLOYMENT: {ev.DEPLOYED, ev.BLOCKED, ev.READY_FOR_SPEC, ev.READY_FOR_IMPLEMENTATION},
    ev.BLOCKED: {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION},
    ev.DEPLOYED: set(),
}
```

## deploy-task.py Git Operations

```python
# Find execution branch for task
branch_name = ...  # from execution.started payload

# Find repo local_path
local_path = ...  # from project events

# Checkout target branch
subprocess.run(['git', 'checkout', target_branch], cwd=local_path, check=True)

# Squash merge
subprocess.run(['git', 'merge', '--squash', branch_name], cwd=local_path, check=True)

# Commit
commit_msg = f"feat: {task.title} (task/{task.id})"
subprocess.run(['git', 'commit', '-m', commit_msg], cwd=local_path, check=True)

# Delete execution branch
subprocess.run(['git', 'branch', '-d', branch_name], cwd=local_path, check=True)

# Advance task status
await state_machine.transition(task_id, ev.DEPLOYED)
```

## task-reset.py Logic

```python
# Default reset — back to ready_for_spec
await state_machine.transition(task_id, ev.READY_FOR_SPEC)

# --reuse-spec — find last assigned spec and re-assign
# 1. Find most recent spec.assigned event for task
# 2. Transition task to ready_for_spec
# 3. Transition task through spec_qa to ready_for_implementation
#    with same spec_id in payload
```

## Tasks

- [ ] Fix `VALID_TRANSITIONS` in `core/state_machine.py` — add reset paths and fix ready_for_qa → ready_for_deployment
- [ ] Update `core/tests/test_state_machine.py` — add tests for new reset transitions
- [ ] Create `scripts/approve-qa.py`
- [ ] Create `scripts/deploy-task.py` with squash merge and branch deletion
- [ ] Create `scripts/task-reset.py` with default and `--reuse-spec` modes
- [ ] Run `pytest core/tests/ worker/tests/ -v` — confirm no regressions
- [ ] Run `ruff check .` — fix all errors
- [ ] Manually verify `approve-qa.py` on task `74fab081`
- [ ] Manually verify `task-reset.py` on task `fb0329cd`
- [ ] Commit: `git add -A && git commit -m "spec(13): QA and deployment scripts"`

## Assumptions

- Postgres running on 127.0.0.1:5432, DATABASE_URL set
- Git available in PATH, repo at `local_path` is valid git repo on `develop` branch
- `branch_name` exists in `execution.started` payload for tasks executed after spec 12
- Tasks `74fab081` and `fb0329cd` exist in database for manual verification
- Legacy executions without `branch_name` — `deploy-task.py` exits with clear error if branch not found
- `.venv/bin/python` used for all script invocations

## Verification Commands

```bash
ruff check .
pytest core/tests/ worker/tests/ -v
.venv/bin/python scripts/approve-qa.py --task-id 74fab081-5552-4767-be4e-8ead8b5cce39
.venv/bin/python scripts/task-reset.py --task-id fb0329cd-74c3-49e1-9af1-16afb1d3bd0f
```

## What Exists After This Spec

```
scripts/
  approve-qa.py    — advance task from ready_for_qa to ready_for_deployment
  deploy-task.py   — squash merge execution branch, advance to deployed
  task-reset.py    — reset task backwards for re-execution
core/
  state_machine.py — reset transitions added, ready_for_qa → ready_for_deployment fixed
```

Full manual control over task lifecycle. Tasks can now be approved, deployed, and reset without direct database manipulation.

```

```
