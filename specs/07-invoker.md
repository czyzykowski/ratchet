# Spec 07: Claude Code Invoker

## Objective

Implement the Claude Code invoker — the module that takes an assembled execution context, invokes Claude Code as a subprocess, captures full output to a trace file, parses the result, and returns a structured outcome.

## Success Criteria

- [ ] `core/invoker.py` implements `ClaudeCodeInvoker` class accepting a `traces_dir` path
- [ ] `ClaudeCodeInvoker.invoke(context)` accepts an `ExecutionContext` and returns an `InvocationResult`
- [ ] `invoke()` runs `claude -p <prompt>` in the worktree directory
- [ ] `invoke()` captures full stdout+stderr to `<traces_dir>/<execution_id>.md`
- [ ] `InvocationResult` contains: `execution_id`, `status`, `failure_reason`, `trace_path`
- [ ] `status` is one of: `completed`, `failed`, `crashed`
- [ ] Output containing `COMPLETED:` marker → status `completed`, failure_reason `None`
- [ ] Output containing `BLOCKED:` marker → status `failed`, failure_reason extracted from BLOCKED report
- [ ] Process exits non-zero with no recognizable marker → status `crashed`, failure_reason includes exit code and last 10 lines of output
- [ ] Process exits zero with no recognizable marker → status `failed`, failure_reason "no completion marker found in output"
- [ ] `traces_dir` is created if it does not exist
- [ ] Trace file is always written regardless of outcome — even for crashes
- [ ] `parse_output(output)` is implemented as a module-level function
- [ ] `get_traces_dir()` is implemented as a module-level function — returns `RATCHET_TRACES_DIR` env var if set, otherwise `$XDG_DATA_HOME/ratchet/traces/` (defaults to `~/.local/share/ratchet/traces/`)
- [ ] Unit tests in `core/tests/test_invoker.py` mock `subprocess.run` — no real Claude Code invocation
- [ ] Unit tests cover all four outcome cases: completed, blocked/failed, crashed, no marker
- [ ] Unit tests cover trace file written correctly for each outcome
- [ ] Unit tests cover failure reason extraction from BLOCKED report
- [ ] Unit tests cover `get_traces_dir()` returning env var when set
- [ ] Unit tests cover `get_traces_dir()` returning default when env var not set
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ -v` passes with no errors
- [ ] Commit: `git add -A && git commit -m "spec(07): Claude Code invoker"`

## Out of Scope

- Do not implement the worker loop — that is spec 08
- Do not call `complete_execution()` or `fail_execution()` — invoker only invokes and reports
- Do not implement knowledge extraction from traces
- Do not modify database schema or migrations
- Do not modify any existing core modules
- Only create `core/invoker.py` and `core/tests/test_invoker.py`

## Technical Context

- Language: Python 3.12
- `claude` binary must be available in PATH — invoker does not verify this, worker handles missing binary
- Subprocess runs with `cwd=context.worktree_path` — all file operations by Claude Code are relative to worktree
- Allowed tools passed via `--allowedTools` flag: `Bash,Read,Write,Edit,Glob,Grep`
- Full output captured via `subprocess.run(capture_output=True, text=True)`
- Traces directory: `$XDG_DATA_HOME/ratchet/traces/` (defaults to `~/.local/share/ratchet/traces/`) by default, `RATCHET_TRACES_DIR` env var overrides
- Existing files:
  - `core/context_assembler.py` — `ExecutionContext` dataclass with prompt and worktree_path
  - `core/models.py` — reference for UUID usage

## InvocationResult Dataclass

```python
@dataclass
class InvocationResult:
    execution_id: UUID
    status: str                 # 'completed' | 'failed' | 'crashed'
    failure_reason: str | None  # None on success, descriptive string on failure/crash
    trace_path: str             # absolute path to trace file
```

## Claude Code Command

```bash
claude -p "<prompt>" --allowedTools "Bash,Read,Write,Edit,Glob,Grep"
```

Run with:

- `cwd=context.worktree_path`
- `capture_output=True`
- `text=True`
- No timeout in v1 — watchdog is handled separately

## Output Parsing Rules

```
If "COMPLETED:" appears anywhere in output:
    → status = 'completed', failure_reason = None

Else if "BLOCKED:" appears anywhere in output:
    → status = 'failed'
    → failure_reason = text between "BLOCKED:" and next blank line
      (or full remaining output if no blank line found)

Else if process exit code != 0:
    → status = 'crashed'
    → failure_reason = f"process exited with code {returncode}. Last output:\n{last_10_lines}"

Else (exit code 0, no marker):
    → status = 'failed'
    → failure_reason = "no completion marker found in output"
```

## Module-Level Functions

```python
def get_traces_dir() -> str:
    """
    Return traces directory path.
    Uses RATCHET_TRACES_DIR env var if set, otherwise $XDG_DATA_HOME/ratchet/traces/ (defaults to ~/.local/share/ratchet/traces/)
    Creates directory if it does not exist.
    Returns absolute path as string.
    """

def parse_output(output: str, returncode: int) -> tuple[str, str | None]:
    """
    Parse Claude Code output to determine invocation outcome.
    Returns (status, failure_reason) tuple.
    Status is one of: 'completed', 'failed', 'crashed'
    failure_reason is None for completed, descriptive string otherwise.
    """
```

## ClaudeCodeInvoker Interface

```python
class ClaudeCodeInvoker:
    def __init__(self, traces_dir: str | None = None) -> None:
        """
        If traces_dir is None, use get_traces_dir() to determine path.
        Create traces_dir if it does not exist.
        """

    def invoke(self, context: ExecutionContext) -> InvocationResult:
        """
        Invoke Claude Code with assembled context.
        1. Build command with prompt and allowed tools
        2. Run subprocess with cwd=context.worktree_path
        3. Write full output (stdout + stderr) to <traces_dir>/<execution_id>.md
        4. Call parse_output(output, returncode) to determine status
        5. Return InvocationResult
        Note: invoke() is synchronous — subprocess.run() blocks until completion.
        """
```

## Trace File Format

```markdown
# Execution Trace: <execution_id>

# Task: <task_id>

# Spec: <spec_id>

# Started: <ISO timestamp>

<full stdout + stderr output>
```

## Test Scenarios to Cover

```
Output parsing:
- Output with "COMPLETED:" → status completed, failure_reason None
- Output with "BLOCKED:\nReason: missing env var" → status failed, failure_reason extracted
- Exit code 1, no marker → status crashed, failure_reason includes exit code
- Exit code 0, no marker → status failed, "no completion marker found"

Trace file:
- Trace file created at correct path for each outcome
- Trace file contains full output
- Trace file header contains execution_id

get_traces_dir():
- RATCHET_TRACES_DIR set → returns that path
- RATCHET_TRACES_DIR not set → returns $XDG_DATA_HOME/ratchet/traces/ (defaults to ~/.local/share/ratchet/traces/)
- Directory created if not exists

InvocationResult:
- trace_path in result matches actual file written
- execution_id in result matches context.execution_id
```

## Tasks

- [ ] Create `core/invoker.py` with `InvocationResult` dataclass, `get_traces_dir()`, `parse_output()`, and `ClaudeCodeInvoker` class
- [ ] Implement `get_traces_dir()` — check env var, fall back to default, create dir if needed
- [ ] Implement `parse_output(output, returncode)` — apply parsing rules in order as specified
- [ ] Implement `ClaudeCodeInvoker.__init__()` — resolve and create traces dir
- [ ] Implement `ClaudeCodeInvoker.invoke()` — build command, run subprocess, write trace, parse output, return result
- [ ] Create `core/tests/test_invoker.py` mocking `subprocess.run` for all scenarios
- [ ] Run `pytest core/tests/ -v` and confirm all tests pass
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(07): Claude Code invoker"`

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- `claude` binary availability is not checked by the invoker — missing binary will surface as a crashed status
- No timeout on subprocess in v1 — watchdog concern is deferred
- `invoke()` is synchronous — the worker will run it in a thread or process if async is needed
- Trace files are append-only in practice — execution_id uniqueness guarantees no collisions
- `RATCHET_TRACES_DIR` is not set in test environment — tests use tmp_path for trace output

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
```

## What Exists After This Spec

```
core/
  invoker.py            — InvocationResult, get_traces_dir, parse_output, ClaudeCodeInvoker
  tests/
    test_invoker.py     — full unit test suite, subprocess mocked
$XDG_DATA_HOME/ratchet/traces/ (defaults to ~/.local/share/ratchet/traces/)      — created on first real invocation
```

Claude Code invocation is fully implemented and tested in isolation. The invoker is a pure input/output module — it takes a context, produces a result, writes a trace. No state machine involvement. Worker loop in spec 08 connects everything together.
