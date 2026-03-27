# System Reliability: Orphan Cleanup and Failure Classification

## Problem

Three systemic issues cause tasks to get stuck or waste resources:

1. **Orphaned executions** — When a worker disconnects mid-execution (WebSocket drop, process crash, machine reboot), the execution remains in `running` status permanently. There are currently 69 orphaned executions spanning 17 days. These pollute the execution history and make it hard to understand task state.

2. **Infrastructure errors exhaust QA retries** — The QA fix loop treats all failures identically. When the error is in the environment (missing Nix store paths, broken dylibs, disk full), Claude wastes 3 fix attempts trying to repair code that isn't broken. The pheme Calendar monitoring task burned 8 QA executions on a `dlopen` failure that no code change could fix.

3. **False BLOCKED detection** — The invoker's BLOCKED/COMPLETED marker parser can be triggered by subprocess output (e.g., npm install logs), causing successful implementations to be marked as blocked.

## Tasks

### Task 1: Execution timeout reaper

Add a periodic cleanup that runs inside the dispatch loop (or as a separate async task in the web lifespan).

**Requirements:**
- Every 5 minutes, find all executions in `running` status where `started_at < now() - interval '2 hours'` and no worker is currently connected for that execution
- Emit `execution.completed` event with `status: "failed"` and `failure_reason: "execution timed out (worker disconnected)"`
- Refresh the `current_executions` materialized view after cleanup
- Log each reaped execution at WARNING level: `"Reaped orphaned execution {id} for task {task_id} (started {age} ago)"`
- Do NOT touch executions where the assigned worker is still connected (check against `WorkerRegistry`)

**Location:** New function in `orchestrator/dispatcher.py` called from the existing `dispatch_loop`, or as a parallel async task in `web/app.py` lifespan.

**Tests:** Add test in `orchestrator/tests/` using `InMemoryStore` that:
- Creates an execution event with `started_at` 3 hours ago
- Runs the reaper
- Asserts the execution is now `failed` with the timeout reason

### Task 2: Classify QA failures as infrastructure vs. code errors

Before attempting QA fixes, classify the failure to decide whether a fix attempt is worthwhile.

**Requirements:**
- Add a function `classify_qa_failure(failure_reason: str) -> Literal["code", "infra", "system"]` in `orchestrator/sequencer.py` (or a new `orchestrator/failure_classifier.py`)
- Infrastructure patterns (should NOT attempt fix):
  - `dlopen` / `Library not loaded` — dynamic library missing
  - `nix/store` path references — Nix store garbage collected
  - `ENOSPC` — disk full
  - `permission denied` on system paths
  - `create_worktree` failures — git worktree issues
- Code patterns (SHOULD attempt fix):
  - Test failures (`FAILED`, `AssertionError`)
  - Lint errors (`ruff`, `flake8`)
  - Type errors (`mypy`)
  - Syntax errors
- When classification is `infra`, skip all fix attempts and immediately block the task with failure reason prefixed: `[INFRA] {original_reason}`
- When classification is `code`, proceed with existing fix loop (up to 3 attempts)

**Location:** Called in `PipelineSequencer._handle_qa_result()` before deciding to retry.

**Tests:** Add test in `orchestrator/tests/` that:
- Asserts `classify_qa_failure("dlopen(...Library not loaded...)")` returns `"infra"`
- Asserts `classify_qa_failure("FAILED test_something")` returns `"code"`
- Asserts `classify_qa_failure("pipeline crashed")` returns `"system"`

### Task 3: Harden BLOCKED marker detection in invoker

Make the COMPLETED/BLOCKED detection more precise to prevent false positives from subprocess output.

**Requirements:**
- In `core/invoker.py`, the marker detection should only match explicit markers from Claude's output, not from arbitrary subprocess output embedded in the response
- The markers should be matched as: a line starting with exactly `COMPLETED` or `BLOCKED:` (with optional whitespace before), not as substring matches within longer output
- Add a test that verifies npm install output containing "BLOCKED" does NOT trigger the blocked detection
- Add a test that verifies `"COMPLETED"` on its own line DOES trigger completed detection

**Location:** `core/invoker.py` — the `_parse_result()` or equivalent method.

**Tests:** Add tests in `core/tests/` covering:
- Subprocess output with "BLOCKED" as substring → should NOT match
- Clean `COMPLETED` marker → should match
- `BLOCKED: reason here` on its own line → should match

### Task 4: Recover execution status on orchestrator restart

When the orchestrator restarts, it already recovers task status via `orchestrator_restart_recovery`. Extend this to also clean up execution status.

**Requirements:**
- During startup recovery, find all executions in `running` status
- For each, check if the assigned worker is connected (via `WorkerRegistry`)
- If the worker is NOT connected, emit `execution.completed` with `status: "failed"` and `failure_reason: "execution abandoned (orchestrator restart recovery)"`
- This runs once at startup, before the dispatch loop begins

**Location:** Extend the existing recovery logic in `web/app.py` lifespan or `orchestrator/dispatcher.py`.

## Verification

After implementation:
1. Run `python scripts/diagnose.py` — orphaned executions should be 0
2. Run `python scripts/diagnose.py --fix-orphans` should be a no-op
3. The dispatch loop should not re-dispatch tasks whose executions timed out (they should be properly blocked or back in ready_for_implementation)
4. All existing tests pass: `pytest core/tests/ orchestrator/tests/ web/tests/ worker/tests/ -v`
5. Type check: `mypy core/ orchestrator/ web/`
6. Lint: `ruff check core/ orchestrator/ web/`
