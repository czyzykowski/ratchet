"""Claude Code invoker: runs Claude Code as a subprocess and captures the result."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from core.claude_subprocess import ClaudeRequest, StreamingHandle, start
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


# Line-anchored patterns for COMPLETED/BLOCKED markers.
# Match only when the marker appears at the start of a line (with optional whitespace),
# NOT as a substring within longer output (e.g., npm install logs).
_COMPLETED_RE = re.compile(r"^\s*COMPLETED:", re.MULTILINE)
_BLOCKED_RE = re.compile(r"^\s*BLOCKED:", re.MULTILINE)


def has_completed_marker(output: str) -> bool:
    """Check if output contains a line-anchored COMPLETED: marker."""
    return _COMPLETED_RE.search(output) is not None


def has_blocked_marker(output: str) -> str | None:
    """Check if output contains a line-anchored BLOCKED: marker.

    Returns the reason text after BLOCKED: if found, None otherwise.
    """
    match = _BLOCKED_RE.search(output)
    if match is None:
        return None
    after_blocked = output[match.end():]
    blank_line_idx = after_blocked.find("\n\n")
    if blank_line_idx != -1:
        return after_blocked[:blank_line_idx].strip()
    return after_blocked.strip()


def parse_output(output: str, returncode: int) -> tuple[str, str | None]:
    """Parse Claude Code output to determine invocation outcome.

    Returns (status, failure_reason) tuple.
    Status is one of: 'completed', 'failed', 'crashed'
    failure_reason is None for completed, descriptive string otherwise.

    Markers are line-anchored: COMPLETED: or BLOCKED: must appear at the start
    of a line (with optional leading whitespace) to be recognized. Substring
    matches within longer output (e.g., npm install logs) are ignored.
    """
    if has_completed_marker(output):
        return "completed", None

    blocked_reason = has_blocked_marker(output)
    if blocked_reason is not None:
        return "failed", blocked_reason

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
        self._handle: StreamingHandle | None = None
        self._handle_lock = threading.Lock()

    def terminate(self) -> None:
        """Terminate the currently running subprocess, if any."""
        with self._handle_lock:
            if self._handle is not None:
                self._handle.terminate()

    def invoke(
        self,
        context: ExecutionContext,
        *,
        allow_project_root: bool = False,
    ) -> InvocationResult:
        """Invoke Claude Code with assembled context.

        1. Build ClaudeRequest and call start()
        2. Run watchdog thread monitoring handle.get_last_activity()
        3. Wait for completion via handle.wait()
        4. Build trace content and save via store.save_trace()
        5. Call parse_output(output, returncode) to determine status
        6. Return InvocationResult

        Safety: unless allow_project_root=True, raises ValueError if worktree_path
        does not appear to be inside a .worktrees/ directory.
        """
        wt = os.path.abspath(context.worktree_path)
        if not allow_project_root and "/.worktrees/" not in wt:
            raise ValueError(
                f"Refusing to invoke in project root ({wt}). "
                "Execution must happen inside a .worktrees/ directory. "
                "Pass allow_project_root=True to override (merge only)."
            )

        request = ClaudeRequest(
            prompt=context.prompt,
            cwd=context.worktree_path,
            model=_MODEL,
            allowed_tools=_ALLOWED_TOOLS,
        )

        handle = start(request)
        with self._handle_lock:
            self._handle = handle

        stop_event = threading.Event()
        watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(
                context.execution_id,
                handle.get_last_activity,
                stop_event,
                self.watchdog_timeout,
            ),
            daemon=True,
        )
        watchdog_thread.start()
        # TODO: watchdog support for non-streaming sandbox backends
        # When a real sandbox backend is added, Sandbox.start() blocks until
        # completion with no streaming output. The watchdog will fire false
        # "no activity" warnings. Options: (1) disable watchdog when sandbox
        # is set, (2) add a Sandbox.supports_streaming property, or
        # (3) have sandbox backends periodically report activity.

        result = handle.wait()

        with self._handle_lock:
            self._handle = None
        stop_event.set()
        watchdog_thread.join()

        output = result.output

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

        status, failure_reason = parse_output(output, result.returncode)

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
