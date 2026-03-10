"""Claude Code invoker: runs Claude Code as a subprocess and captures the result."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import PIPE
from uuid import UUID

from core.context_assembler import ExecutionContext

_ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"


@dataclass
class InvocationResult:
    execution_id: UUID
    status: str  # 'completed' | 'failed' | 'crashed'
    failure_reason: str | None  # None on success, descriptive string otherwise
    trace_path: str  # absolute path to trace file


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
        traces_dir: str | None = None,
        watchdog_timeout: int = 300,
    ) -> None:
        """If traces_dir is None, use get_traces_dir() to determine path.
        Create traces_dir if it does not exist.
        """
        if traces_dir is None:
            self.traces_dir = get_traces_dir()
        else:
            path = Path(traces_dir)
            path.mkdir(parents=True, exist_ok=True)
            self.traces_dir = str(path.resolve())
        assert Path(self.traces_dir).is_dir(), f"traces dir not created: {self.traces_dir}"
        self.watchdog_timeout = watchdog_timeout

    def invoke(self, context: ExecutionContext) -> InvocationResult:
        """Invoke Claude Code with assembled context.

        1. Build command with prompt and allowed tools
        2. Run subprocess with cwd=context.worktree_path, reading stdout/stderr via threads
        3. Write full output (stdout + stderr) to <traces_dir>/<execution_id>.md
        4. Call parse_output(output, returncode) to determine status
        5. Return InvocationResult
        """
        cmd = ["claude", "-p", context.prompt, "--allowedTools", _ALLOWED_TOOLS]

        # Strip CLAUDECODE so nested sessions don't fail when worker runs inside Claude Code
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        lock = threading.Lock()
        last_activity: list[float] = [time.monotonic()]

        def get_activity() -> float:
            with lock:
                return last_activity[0]

        def read_stream(stream: object, lines: list[str]) -> None:
            for line in stream:  # type: ignore[union-attr]
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
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            env=env,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_lines))
        t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_lines))
        t_out.start()
        t_err.start()
        t_out.join()
        t_err.join()

        returncode = proc.wait()

        stop_event.set()
        watchdog_thread.join()

        output = "".join(stdout_lines) + "".join(stderr_lines)

        # Write trace file
        trace_path = str(Path(self.traces_dir) / f"{context.execution_id}.md")
        started_at = datetime.now(UTC).isoformat()
        header = (
            f"# Execution Trace: {context.execution_id}\n\n"
            f"# Task: {context.task_id}\n\n"
            f"# Spec: {context.spec_id}\n\n"
            f"# Started: {started_at}\n\n"
        )
        Path(trace_path).write_text(header + output)

        status, failure_reason = parse_output(output, returncode)

        return InvocationResult(
            execution_id=context.execution_id,
            status=status,
            failure_reason=failure_reason,
            trace_path=trace_path,
        )
