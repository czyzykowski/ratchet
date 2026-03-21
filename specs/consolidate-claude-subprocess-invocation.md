# Consolidate Claude Subprocess Invocation

## Objective

Create `core/claude_subprocess.py` with a unified API (`run()` and `start()`) that replaces the 5 different Claude subprocess invocation patterns scattered across `core/invoker.py`, `core/compiler.py`, `core/review_engine.py`, and `worker/pipelines/qa.py`.

## Success Criteria

- [ ] `core/claude_subprocess.py` exists with `ClaudeRequest`, `ClaudeResult`, `StreamingHandle`, `run()`, and `start()`
- [ ] `core/claude_subprocess.py` imports only from stdlib (subprocess, threading, os, pathlib, time, dataclasses) -- zero ratchet dependencies
- [ ] `run()` always delivers prompt via stdin pipe (never CLI arg `-p <prompt>`)
- [ ] `run()` always strips `CLAUDECODE` from environment
- [ ] `run()` always auto-detects nix wrapping by checking `(Path(cwd) / "flake.nix").exists()`
- [ ] `run()` accepts optional `on_stdout_line` callback for streaming
- [ ] `run()` respects optional `timeout` field on `ClaudeRequest`
- [ ] `start()` returns `StreamingHandle` with `get_last_activity()`, `wait()`, and `terminate()`
- [ ] `start()` tracks activity timestamps on every stdout/stderr line
- [ ] `core/invoker.py` uses `start()` instead of raw `subprocess.Popen` -- subprocess plumbing removed
- [ ] `core/compiler.py` uses `run(on_stdout_line=...)` instead of raw `subprocess.Popen`
- [ ] `core/review_engine.py` uses `run()` instead of raw `subprocess.run` -- `USE_NIX_DEVELOP` env var removed
- [ ] `worker/pipelines/qa.py` uses `run()` instead of raw `subprocess.run` for the Claude review step
- [ ] `ClaudeCodeInvoker` public API preserved: `invoke()`, `terminate()` signatures unchanged
- [ ] `InvocationResult`, `parse_output()`, `_scan_session_jsonl()`, `get_traces_dir()`, `_watchdog_loop()` remain in `core/invoker.py`
- [ ] `core/tests/test_claude_subprocess.py` has tests for `run()` and `start()` mocking `subprocess.Popen`
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v`
- [ ] Lint passes: `ruff check .`
- [ ] Type check passes: `mypy core/ worker/ web/`

## Out of Scope

- Do not change `core/merge.py` -- it already delegates to `invoker.invoke()`
- Do not change the watchdog thread logic -- it stays in `core/invoker.py`
- Do not change trace saving logic -- it stays in `core/invoker.py`
- Do not change output parsing (`parse_output`, `_scan_session_jsonl`) -- stays in `core/invoker.py`
- Do not change `InvocationResult` or `ExecutionContext` types
- Do not change `core/models_config.py` model constants
- Do not add async to `claude_subprocess.py` -- callers use `asyncio.to_thread` as needed
- Do not add ABC, Protocol, or base classes -- keep it concrete functions
- Do not change the `core/context_assembler.py` module

## Technical Context

- Stack: Python 3.12, asyncio, subprocess, threading
- Entry point: `core/claude_subprocess.py` (new file)
- Related files:
  - `core/invoker.py` (295 lines) -- `ClaudeCodeInvoker.invoke()` at line 166: Popen + stdin pipe, reader threads, watchdog, trace saving, marker parsing
  - `core/compiler.py` lines 22-45 -- `run_claude()`: Popen with prompt as CLI arg, streaming stdout line-by-line
  - `core/review_engine.py` lines 37-46 -- `ReviewEngine.analyze()`: subprocess.run with prompt as CLI arg, timeout=120, `USE_NIX_DEVELOP` env var
  - `worker/pipelines/qa.py` lines 185-193 -- inline `subprocess.run` for Claude review step
  - `core/models_config.py` -- `WORKER_MODEL = "claude-sonnet-4-6"`, `CHAT_MODEL = "claude-opus-4-6"`
  - `core/tests/test_invoker.py` (401 lines) -- mocks `subprocess.Popen` throughout

## Data Examples

**ClaudeRequest:**
```python
ClaudeRequest(
    prompt="Implement the feature described in...",
    cwd="/home/user/project/.worktrees/exec-abc123",
    model="claude-sonnet-4-6",
    allowed_tools="Bash,Read,Write,Edit,Glob,Grep",
)
```

**ClaudeResult:**
```python
ClaudeResult(
    stdout="I'll implement this...\nCOMPLETED: done",
    stderr="",
    returncode=0,
)
```

**StreamingHandle usage (invoker):**
```python
handle = start(request)
# Watchdog uses handle.get_last_activity() to detect silence
activity = handle.get_last_activity()  # monotonic timestamp
# Block until process exits
result = handle.wait()  # returns ClaudeResult
# Or terminate early
handle.terminate()
```

## Tasks

### Task 1: Create `core/claude_subprocess.py` with `run()` + test

Create `core/claude_subprocess.py` with:
- `ClaudeRequest` frozen dataclass: `prompt: str`, `cwd: str`, `model: str`, `allowed_tools: str = ""`, `timeout: float | None = None`
- `ClaudeResult` dataclass: `stdout: str`, `stderr: str`, `returncode: int`, plus `output` property returning `stdout + stderr`
- `run(request, *, on_stdout_line=None) -> ClaudeResult` function that:
  1. Builds command: `["claude", "-p", "--model", request.model, "--allowedTools", request.allowed_tools]`
  2. If `(Path(request.cwd) / "flake.nix").exists()`: prepend `["nix", "develop", "--command"]`
  3. Build env: `{k: v for k, v in os.environ.items() if k != "CLAUDECODE"}`
  4. Launch `subprocess.Popen(cmd, cwd=request.cwd, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, env=env)`
  5. Write `request.prompt` to stdin, close stdin
  6. Read stdout/stderr via threads (to avoid pipe deadlock)
  7. If `on_stdout_line` is provided, call it for each stdout line as it arrives
  8. If `request.timeout` is set, use a timer thread to terminate the process after timeout
  9. Wait for process, return `ClaudeResult`

Create `core/tests/test_claude_subprocess.py` with tests:
- Test `run()` happy path: mock Popen, assert stdout/stderr/returncode returned correctly
- Test `run()` with `on_stdout_line`: assert callback called for each line
- Test `run()` with timeout: assert process terminated after timeout
- Test nix wrapping: when `flake.nix` exists in cwd, command is prefixed with `nix develop --command`
- Test CLAUDECODE stripped: assert env dict passed to Popen does not contain `CLAUDECODE`
- Test `ClaudeResult.output` property: returns `stdout + stderr`

Acceptance:
- `core/claude_subprocess.py` exists with `ClaudeRequest`, `ClaudeResult`, `run()`
- Tests pass: `.venv/bin/python -m pytest core/tests/test_claude_subprocess.py -v`
- Lint passes: `ruff check core/claude_subprocess.py`

### Task 2: Add `start()` + `StreamingHandle` to `core/claude_subprocess.py` + test

Add to `core/claude_subprocess.py`:
- `StreamingHandle` class with:
  - `get_last_activity() -> float`: returns monotonic timestamp of last stdout/stderr line
  - `wait() -> ClaudeResult`: blocks until process exits, joins reader threads, returns result
  - `terminate() -> None`: sends SIGTERM to the subprocess
- `start(request) -> StreamingHandle` function that:
  1. Builds command and env same as `run()`
  2. Launches Popen with stdin pipe
  3. Writes prompt to stdin, closes stdin
  4. Starts reader threads for stdout/stderr that track last-activity timestamps
  5. Returns `StreamingHandle` immediately (non-blocking)

Refactor `run()` to internally use `start()`: `handle = start(request); result = handle.wait()` -- then apply `on_stdout_line` and timeout on top.

Add tests:
- Test `start()` returns handle, `wait()` returns ClaudeResult
- Test `get_last_activity()` returns a monotonic timestamp that updates as output arrives
- Test `terminate()` kills the subprocess

Acceptance:
- `StreamingHandle` and `start()` exist
- `run()` is implemented in terms of `start()`
- Tests pass: `.venv/bin/python -m pytest core/tests/test_claude_subprocess.py -v`

### Task 3: Migrate `core/invoker.py` to use `start()`

Replace the subprocess plumbing in `ClaudeCodeInvoker.invoke()` (lines 191-257) with `start()`:

**Before** (lines 191-257): builds cmd, env, Popen, stdin write, reader threads, watchdog threads, proc.wait
**After**: builds `ClaudeRequest`, calls `start()`, uses `handle.get_last_activity` for watchdog, `handle.wait()` for result, `handle.terminate` for `ClaudeCodeInvoker.terminate()`

Keep in `invoker.py`:
- Safety check for worktree path (lines 184-190)
- `_ALLOWED_TOOLS` and `_MODEL` constants
- Watchdog thread creation and management (lines 213-257)
- Trace building and saving (lines 261-277)
- `parse_output()` call and JSONL fallback (lines 279-287)
- `InvocationResult` construction (lines 289-294)

Remove from `invoker.py`:
- Direct `subprocess.Popen` call
- Manual stdin/stdout/stderr handling
- `read_stream` inner function
- Manual env construction
- Manual nix wrapping logic

Update `ClaudeCodeInvoker.terminate()` to call `handle.terminate()` on the stored handle instead of `self._proc.terminate()`.

Update `core/tests/test_invoker.py`: change mock target from `subprocess.Popen` to `core.claude_subprocess.start` (or `core.claude_subprocess.run`). The mock should return a fake `StreamingHandle` or `ClaudeResult`.

Acceptance:
- `invoker.py` no longer imports `subprocess` directly
- `invoker.py` no longer contains Popen calls, reader threads, or env construction
- `ClaudeCodeInvoker.invoke()` and `terminate()` work identically to before
- All invoker tests pass: `.venv/bin/python -m pytest core/tests/test_invoker.py -v`
- All worker tests pass: `.venv/bin/python -m pytest worker/tests/ -v`

### Task 4: Migrate `core/compiler.py` to use `run()`

Replace `compiler.py` `run_claude()` function (lines 22-45) to use `core.claude_subprocess.run()`:

**Before**: builds cmd with prompt as CLI arg, Popen with streaming stdout, manual line iteration
**After**: builds `ClaudeRequest`, calls `run(request, on_stdout_line=lambda line: print(line, end="", flush=True))`

The `debug` parameter that prints the prompt to stderr (lines 24-27) stays -- it happens before the `run()` call.

Update tests in `core/tests/test_compiler.py` to mock `core.claude_subprocess.run` instead of `subprocess.Popen` or `core.compiler.run_claude`.

Acceptance:
- `compiler.py` `run_claude()` uses `core.claude_subprocess.run()` internally
- `compiler.py` no longer imports `subprocess` directly
- Prompt is now delivered via stdin (was CLI arg) -- this is a behavior change that is strictly safer
- All compiler tests pass: `.venv/bin/python -m pytest core/tests/test_compiler.py -v`

### Task 5: Migrate `core/review_engine.py` to use `run()`

Replace `ReviewEngine.analyze()` subprocess call (lines 37-46) with `core.claude_subprocess.run()`:

**Before**: builds cmd with prompt as CLI arg, checks `USE_NIX_DEVELOP` env var, calls `subprocess.run(timeout=120)`
**After**: builds `ClaudeRequest(timeout=120)`, calls `run(request)`

Remove `USE_NIX_DEVELOP` env var check -- nix wrapping is now auto-detected via `flake.nix`.

Update tests in `core/tests/test_review_engine.py` to mock `core.claude_subprocess.run` instead of `subprocess.run`.

Acceptance:
- `review_engine.py` uses `core.claude_subprocess.run()` internally
- `review_engine.py` no longer imports `subprocess` directly
- `USE_NIX_DEVELOP` env var is no longer checked
- All review engine tests pass: `.venv/bin/python -m pytest core/tests/test_review_engine.py -v`

### Task 6: Migrate `worker/pipelines/qa.py` Claude review to use `run()`

Replace the inline `subprocess.run` call for Claude review (lines 185-193) with `core.claude_subprocess.run()`:

**Before**: `_subprocess.run(["claude", "-p", "--model", WORKER_MODEL, "--allowedTools", "Bash,Read,Glob,Grep"], input=review_prompt, ...)`
**After**: builds `ClaudeRequest`, calls `await asyncio.to_thread(run, request)`

Remove `import subprocess as _subprocess` if no longer used elsewhere in the file.

Update test patches in `worker/tests/test_dispatcher.py` (and any other test files that mock the QA review subprocess) to target `core.claude_subprocess.run` instead of `worker.pipelines.qa._subprocess.run`.

Acceptance:
- `qa.py` uses `core.claude_subprocess.run()` for the Claude review step
- `qa.py` no longer imports `subprocess` for Claude invocation
- All QA tests pass: `.venv/bin/python -m pytest worker/tests/ -v`

### Task 7: Clean up and run full validation

- Remove any dead imports from modified files
- Verify `core/claude_subprocess.py` has zero ratchet imports (only stdlib)
- Verify `core/invoker.py` no longer imports `subprocess` (or only imports it for non-Claude use)

Run full validation:
```bash
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```

Fix any type errors, lint issues, or test failures.

Acceptance:
- All tests pass (zero failures)
- `ruff check .` passes (zero lint errors)
- `mypy core/ worker/ web/` passes (zero type errors)

## Test Requirements

- Test framework: pytest with pytest-asyncio
- All tests use mock `subprocess.Popen` -- no real Claude binary needed
- New tests in `core/tests/test_claude_subprocess.py` should follow the pattern in `core/tests/test_invoker.py`:
  - Create a `_fake_popen()` helper that returns a mock Popen with configurable stdout/stderr/returncode
  - Use `unittest.mock.patch` to mock `subprocess.Popen`
- Test scenarios for `run()`:
  - Happy path: prompt delivered, stdout/stderr collected, returncode returned
  - Streaming callback: `on_stdout_line` called per line
  - Timeout: process terminated after deadline
  - Nix wrapping: command prefixed when `flake.nix` exists
  - Env sanitization: CLAUDECODE stripped from env
- Test scenarios for `start()`:
  - Returns StreamingHandle immediately
  - `get_last_activity()` updates on output
  - `wait()` blocks and returns ClaudeResult
  - `terminate()` kills subprocess

## Assumptions

- `claude` binary is available in PATH (or via `nix develop --command`)
- `-p` flag without a value argument tells Claude to read prompt from stdin
- All callers currently using CLI arg prompt delivery (`-p <prompt>`) will work identically with stdin delivery
- `flake.nix` presence in `cwd` is the correct and sufficient signal for nix wrapping across all callers
- `CLAUDECODE` env var should always be stripped (no caller needs it preserved)

## Verification Commands

```bash
.venv/bin/python -m pytest core/tests/test_claude_subprocess.py -v
.venv/bin/python -m pytest core/tests/test_invoker.py -v
.venv/bin/python -m pytest core/tests/test_compiler.py -v
.venv/bin/python -m pytest core/tests/test_review_engine.py -v
.venv/bin/python -m pytest core/tests/ web/tests/ worker/tests/ -v
ruff check .
mypy core/ worker/ web/
```
