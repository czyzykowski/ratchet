"""Claude Code invoker: runs Claude Code as a subprocess and captures the result."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import PIPE
from uuid import UUID

from core.context_assembler import ExecutionContext
from core.models import ExecutionTrace
from core.models_config import WORKER_MODEL
from core.store import Store

_ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
_MODEL = WORKER_MODEL


@dataclass
class InvocationResult:
    execution_id: UUID
    status: str  # 'completed' | 'failed' | 'crashed'
    failure_reason: str | None  # None on success, descriptive string otherwise
    trace_id: UUID  # execution_id used as trace reference (one trace per execution)


def get_traces_dir() -> str:
    """Return traces directory path.

    Uses RATCHET_TRACES_DIR env var if set, otherwise $XDG_DATA_HOME/ratchet/traces/
    (defaults to ~/.local/share/ratchet/traces/).
    Creates directory if it does not exist.
    Returns absolute path as string.
    """
    env_dir = os.environ.get("RATCHET_TRACES_DIR")
    if env_dir:
        traces_dir = Path(env_dir)
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        traces_dir = Path(xdg_data_home) / "ratchet" / "traces"

    traces_dir.mkdir(parents=True, exist_ok=True)
    return str(traces_dir.resolve())


def _scan_session_jsonl(worktree_path: str) -> str:
    """Scan the most recent Claude session JSONL for assistant text content.

    Claude stores conversation transcripts at:
    ~/.claude/projects/<slug>/*.jsonl
    where slug = absolute worktree path with '/' replaced by '-'.

    Returns all assistant text joined together, or '' if not found.
    """
    import glob as _glob
    import json as _json

    # Claude slugifies paths by replacing both '/' and '.' with '-'
    slug = worktree_path.replace("/", "-").replace(".", "-")
    project_dir = Path.home() / ".claude" / "projects" / slug
    if not project_dir.exists():
        return ""
    jsonl_files = sorted(_glob.glob(str(project_dir / "*.jsonl")), key=os.path.getmtime)
    if not jsonl_files:
        return ""
    texts: list[str] = []
    try:
        with open(jsonl_files[-1]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                for block in obj.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
    except OSError:
        return ""
    return "\n".join(texts)


def parse_output(output: str, returncode: int) -> tuple[str, str | None]:
    """Parse Claude Code output to determine invocation outcome.

    Returns (status, failure_reason) tuple.
    Status is one of: 'completed', 'failed', 'crashed'
    failure_reason is None for completed, descriptive string otherwise.
    """
    if "COMPLETED:" in output:
        return "completed", None

    if "BLOCKED:" in output:
        idx = output.index("BLOCKED:")
        after_blocked = output[idx + len("BLOCKED:"):]
        # Extract text up to the next blank line
        blank_line_idx = after_blocked.find("\n\n")
        if blank_line_idx != -1:
            failure_reason = after_blocked[:blank_line_idx].strip()
        else:
            failure_reason = after_blocked.strip()
        return "failed", failure_reason

    if returncode != 0:
        lines = output.splitlines()
        last_10 = "\n".join(lines[-10:]) if lines else ""
        failure_reason = f"process exited with code {returncode}. Last output:\n{last_10}"
        return "crashed", failure_reason

    return "failed", "no completion marker found in output"


def _watchdog_loop(
    execution_id: UUID,
    get_activity: Callable[[], float],
    stop_event: threading.Event,
    threshold: int,
    _interval: int = 30,
) -> None:
    """Watchdog thread body. Warns to stderr when no activity for > threshold seconds."""
    last_warned: float | None = None
    while not stop_event.wait(_interval):
        now = time.monotonic()
        last_activity = get_activity()
        silent_for = now - last_activity
        if silent_for > threshold:
            if last_warned is None or (now - last_warned) >= threshold:
                n = int(silent_for)
                print(
                    f"[watchdog] task={execution_id} silent for {n}s"
                    f" (threshold {threshold}s)",
                    file=sys.stderr,
                    flush=True,
                )
                last_warned = now


class ClaudeCodeInvoker:
    def __init__(
        self,
        store: Store,
        watchdog_timeout: int = 300,
    ) -> None:
        self._store = store
        self.watchdog_timeout = watchdog_timeout
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._proc_lock = threading.Lock()

    def terminate(self) -> None:
        """Terminate the currently running subprocess, if any."""
        with self._proc_lock:
            if self._proc is not None:
                self._proc.terminate()

    def invoke(self, context: ExecutionContext) -> InvocationResult:
        """Invoke Claude Code with assembled context.

        1. Build command with prompt and allowed tools
        2. Run subprocess with cwd=context.worktree_path, reading stdout/stderr via threads
        3. Build trace content (header + stdout + stderr) and save via store.save_trace()
        4. Call parse_output(output, returncode) to determine status
        5. Return InvocationResult
        """
        claude_cmd = ["claude", "-p", "--model", _MODEL, "--allowedTools", _ALLOWED_TOOLS]
        if os.name != "nt" and (Path(context.worktree_path) / "flake.nix").exists():
            cmd = ["nix", "develop", "--command"] + claude_cmd
        else:
            cmd = claude_cmd

        # Strip CLAUDECODE so nested sessions don't fail when worker runs inside Claude Code
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        lock = threading.Lock()
        last_activity: list[float] = [time.monotonic()]

        def get_activity() -> float:
            with lock:
                return last_activity[0]

        def read_stream(stream: Iterable[str], lines: list[str]) -> None:
            for line in stream:
                lines.append(line)
                with lock:
                    last_activity[0] = time.monotonic()

        stop_event = threading.Event()
        watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(
                context.execution_id,
                get_activity,
                stop_event,
                self.watchdog_timeout,
            ),
            daemon=True,
        )
        watchdog_thread.start()

        proc = subprocess.Popen(
            cmd,
            cwd=context.worktree_path,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            env=env,
        )
        with self._proc_lock:
            self._proc = proc

        assert proc.stdin is not None
        proc.stdin.write(context.prompt)
        proc.stdin.close()

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_lines))
        t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_lines))
        t_out.start()
        t_err.start()
        t_out.join()
        t_err.join()

        returncode = proc.wait()

        with self._proc_lock:
            self._proc = None
        stop_event.set()
        watchdog_thread.join()

        output = "".join(stdout_lines) + "".join(stderr_lines)

        # Build and save trace
        started_at = datetime.now(UTC)
        header = (
            f"# Execution Trace: {context.execution_id}\n\n"
            f"# Task: {context.task_id}\n\n"
            f"# Spec: {context.spec_id}\n\n"
            f"# Started: {started_at.isoformat()}\n\n"
        )
        trace = ExecutionTrace(
            execution_id=context.execution_id,
            task_id=context.task_id,
            spec_id=context.spec_id,
            content=header + output,
            started_at=started_at,
            created_at=started_at,
        )
        self._store.save_trace(trace)

        status, failure_reason = parse_output(output, returncode)

        # Fallback: if stdout lacks a completion marker but the Claude session
        # JSONL has one (e.g. background tasks triggered extra turns after
        # COMPLETED: was output), scan the session transcript directly.
        if status == "failed" and failure_reason == "no completion marker found in output":
            session_text = _scan_session_jsonl(context.worktree_path)
            if session_text:
                status, failure_reason = parse_output(session_text, 0)

        return InvocationResult(
            execution_id=context.execution_id,
            status=status,
            failure_reason=failure_reason,
            trace_id=context.execution_id,
        )
