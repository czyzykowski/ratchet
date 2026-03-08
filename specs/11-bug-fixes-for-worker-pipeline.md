# Spec 11: Worker Pipeline Bug Fixes

## Objective

Fix four bugs discovered during first real pipeline execution: missing debug logging in worker, traces directory not created, failure reason not displayed in review-blocked.py, and missing completion marker instructions in context assembler prompt.

## Success Criteria

### Debug Logging

- [ ] `worker/runner.py` checks `RATCHET_DEBUG=1` environment variable
- [ ] When `RATCHET_DEBUG=1`, worker prints assembled prompt before invocation
- [ ] When `RATCHET_DEBUG=1`, worker prints exact command being run
- [ ] When `RATCHET_DEBUG=1`, worker prints raw output after invocation
- [ ] Debug output prefixed with `[DEBUG]` to distinguish from INFO logs
- [ ] Normal operation (no `RATCHET_DEBUG`) is unchanged — no extra output

### Traces Directory

- [ ] `core/invoker.py` `get_traces_dir()` creates directory with `mkdir(parents=True, exist_ok=True)` before returning path
- [ ] `ClaudeCodeInvoker.__init__()` calls `get_traces_dir()` and verifies directory exists after creation
- [ ] Trace file is written for every invocation including failures
- [ ] Running worker with `RATCHET_DEBUG=1` prints trace file path after invocation
- [ ] `~/.local/share/ratchet/traces/` directory is created on first worker run

### review-blocked.py

- [ ] `scripts/review-blocked.py` correctly reads `failure_reason` from `execution.failed` event payload
- [ ] Running `review-blocked.py` shows failure reason for the existing blocked task `fb0329cd`
- [ ] Failure reason displayed as: `Last failure: <reason>`
- [ ] If no execution events found for blocked task, shows: `Last failure: (no execution recorded)`

### Context Assembler Prompt

- [ ] `core/context_assembler.py` `build_prompt()` includes explicit completion marker instructions
- [ ] Prompt instructs agent to output `COMPLETED: <task>` followed by summary on success
- [ ] Prompt instructs agent to output `BLOCKED: <task>` followed by reason on failure
- [ ] Prompt format matches what `core/invoker.py` `parse_output()` expects
- [ ] Unit tests in `core/tests/test_context_assembler.py` updated to verify prompt contains completion instructions
- [ ] Unit tests verify prompt contains `COMPLETED:` instruction
- [ ] Unit tests verify prompt contains `BLOCKED:` instruction

### General

- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ worker/tests/ -v` passes with no regressions
- [ ] Commit: `git add -A && git commit -m "spec(11): worker pipeline bug fixes"`

## Out of Scope

- Do not fix the blocked CLAUDE.md task — that will be re-run after these fixes
- Do not implement knowledge extraction
- Do not modify database schema or migrations
- Do not modify `core/state_machine.py`, `core/spec_manager.py`, `core/project_manager.py`
- Only modify: `worker/runner.py`, `core/invoker.py`, `scripts/review-blocked.py`, `core/context_assembler.py`, `core/tests/test_context_assembler.py`

## Technical Context

- Language: Python 3.12
- `RATCHET_DEBUG` checked via `os.environ.get('RATCHET_DEBUG') == '1'`
- Debug output uses `print(f'[DEBUG] ...')` — not logging module
- Traces directory: `$XDG_DATA_HOME/ratchet/traces/` — `XDG_DATA_HOME` defaults to `~/.local/share`
- `pathlib.Path.mkdir(parents=True, exist_ok=True)` is the correct way to create nested dirs
- Existing files:
  - `core/invoker.py` — `get_traces_dir()`, `ClaudeCodeInvoker`
  - `core/context_assembler.py` — `build_prompt()`, prompt structure
  - `worker/runner.py` — `run_once()`, calls invoker
  - `scripts/review-blocked.py` — reads blocked tasks and execution events

## Prompt Addition for `build_prompt()`

Add this section to the prompt, between the Spec section and the end:

```
---

## Completion Instructions
When you have finished all tasks and verifications, you MUST output one of these markers:

On success:
```

COMPLETED: <spec title or brief description>
Tasks completed: N/N
Files created/modified:

- <file 1>
- <file 2>

```

On failure or if you cannot complete the task:
```

BLOCKED: <task name that failed>
Reason: <what went wrong>
Missing:

- <item 1>
  User action required:
  <exact steps to unblock>
  Resume: re-run after fixing the above

```

These markers are parsed by the orchestrator. Without them the execution will be marked as failed.
```

## Debug Output to Add in `run_once()`

Add after context assembly and before invocation:

```python
if os.environ.get('RATCHET_DEBUG') == '1':
    print(f'[DEBUG] Assembled prompt ({len(context.prompt)} chars):')
    print(context.prompt[:2000])  # first 2000 chars to avoid overwhelming output
    print(f'[DEBUG] Worktree: {context.worktree_path}')
    print(f'[DEBUG] Command: claude -p <prompt> --allowedTools Bash,Read,Write,Edit,Glob,Grep')
```

Add after invocation:

```python
if os.environ.get('RATCHET_DEBUG') == '1':
    print(f'[DEBUG] Invocation status: {result.status}')
    print(f'[DEBUG] Trace path: {result.trace_path}')
    print(f'[DEBUG] Raw output preview:')
    # Read first 1000 chars of trace file if it exists
    try:
        trace_content = Path(result.trace_path).read_text()[:1000]
        print(trace_content)
    except Exception:
        print('[DEBUG] Could not read trace file')
```

## review-blocked.py Fix

Current bug: script is not correctly finding failure reason from execution events.

The fix: when displaying a blocked task, replay all events to find the most recent `execution.failed` event where `payload['task_id'] == str(task_id)`. Extract `payload['failure_reason']`.

```python
# Find most recent execution.failed event for this task
failure_reason = "(no execution recorded)"
all_events = await store.get_events(task.id, aggregate_type='execution')
# Also need to search by task_id in payload since executions use execution_id as aggregate_id
# Query all execution events and filter by task_id in payload
```

Note: execution events use `execution_id` as `aggregate_id`, not `task_id`. To find executions for a task, the script needs to scan all execution events and filter by `payload['task_id']`. This may require a helper query or scanning recent events. Implement the most straightforward approach.

## Tasks

- [ ] Fix `core/invoker.py` `get_traces_dir()` — add `Path(traces_dir).mkdir(parents=True, exist_ok=True)` before return
- [ ] Fix `core/invoker.py` `ClaudeCodeInvoker.__init__()` — verify directory exists after calling `get_traces_dir()`
- [ ] Fix `core/context_assembler.py` `build_prompt()` — add Completion Instructions section as specified above
- [ ] Update `core/tests/test_context_assembler.py` — add assertions that prompt contains `COMPLETED:` and `BLOCKED:` instruction text
- [ ] Fix `scripts/review-blocked.py` — correctly read failure_reason from execution.failed event payload by scanning execution events filtered by task_id in payload
- [ ] Add debug logging to `worker/runner.py` — check `RATCHET_DEBUG=1`, print prompt preview and command before invocation, print status and trace preview after
- [ ] Run `pytest core/tests/ worker/tests/ -v` and confirm all tests pass
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Manually verify: `python scripts/review-blocked.py` shows failure reason for blocked task `fb0329cd`
- [ ] Commit: `git add -A && git commit -m "spec(11): worker pipeline bug fixes"`

## Assumptions

- Postgres running on 127.0.0.1:5432, DATABASE_URL and TEST_DATABASE_URL set
- Blocked task `fb0329cd` exists in database with one failed execution
- `execution.failed` event payload contains `failure_reason` field — confirmed from database query
- The execution aggregate_id is the execution_id, not task_id — scripts must scan and filter by payload
- `.venv/bin/python` must be used for all script invocations

## Verification Commands

```bash
ruff check .
pytest core/tests/ worker/tests/ -v
.venv/bin/python scripts/review-blocked.py
```

## What Exists After This Spec

All four bugs fixed:

- Worker emits debug output when `RATCHET_DEBUG=1`
- Traces directory created automatically on first run
- `review-blocked.py` shows correct failure reasons
- Context assembler prompt includes completion marker instructions

The CLAUDE.md update task (`fb0329cd`) can now be re-specced and re-run through the worker pipeline with confidence that completion markers will be detected correctly.
