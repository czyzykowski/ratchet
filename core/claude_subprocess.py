"""Unified Claude subprocess invocation.

Provides run() for blocking calls and start() for non-blocking calls
with activity tracking and termination support.

This module has zero ratchet dependencies — stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE


@dataclass(frozen=True)
class ClaudeRequest:
    """Everything needed to invoke Claude as a subprocess."""

    prompt: str
    cwd: str
    model: str
    allowed_tools: str = ""
    timeout: float | None = None


@dataclass
class ClaudeResult:
    """Raw result from a Claude subprocess invocation."""

    stdout: str
    stderr: str
    returncode: int

    @property
    def output(self) -> str:
        """Combined stdout + stderr."""
        return self.stdout + self.stderr


def _build_cmd(request: ClaudeRequest) -> list[str]:
    """Build the claude command, optionally wrapped with nix develop."""
    claude_cmd = [
        "claude", "-p",
        "--model", request.model,
        "--allowedTools", request.allowed_tools,
    ]
    if os.name != "nt" and (Path(request.cwd) / "flake.nix").exists():
        return ["nix", "develop", "--command"] + claude_cmd
    return claude_cmd


def _build_env() -> dict[str, str]:
    """Build environment with CLAUDECODE stripped."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def _read_lines(
    stream: Iterable[str],
    lines: list[str],
    callback: Callable[[str], None] | None = None,
    activity: list[float] | None = None,
) -> None:
    """Read lines from a stream, collecting them and optionally calling a callback."""
    for line in stream:
        lines.append(line)
        if callback is not None:
            callback(line)
        if activity is not None:
            activity[0] = time.monotonic()


class StreamingHandle:
    """Handle to a running Claude process with activity tracking."""

    def __init__(
        self,
        proc: subprocess.Popen,  # type: ignore[type-arg]
        stdout_lines: list[str],
        stderr_lines: list[str],
        threads: list[threading.Thread],
        activity: list[float],
    ) -> None:
        self._proc = proc
        self._stdout_lines = stdout_lines
        self._stderr_lines = stderr_lines
        self._threads = threads
        self._activity = activity

    def get_last_activity(self) -> float:
        """Return monotonic timestamp of last stdout/stderr line."""
        return self._activity[0]

    def wait(self) -> ClaudeResult:
        """Block until process exits, return ClaudeResult."""
        for t in self._threads:
            t.join()
        returncode = self._proc.wait()
        return ClaudeResult(
            stdout="".join(self._stdout_lines),
            stderr="".join(self._stderr_lines),
            returncode=returncode,
        )

    def terminate(self) -> None:
        """Send SIGTERM to the subprocess."""
        self._proc.terminate()


def start(request: ClaudeRequest) -> StreamingHandle:
    """Launch Claude subprocess, return handle for monitoring/waiting.

    Non-blocking: returns immediately after launching the process.
    Use handle.wait() to block until completion.
    Use handle.get_last_activity() for watchdog integration.
    Use handle.terminate() for graceful shutdown.
    """
    cmd = _build_cmd(request)
    env = _build_env()

    proc = subprocess.Popen(
        cmd,
        cwd=request.cwd,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert proc.stdin is not None
    proc.stdin.write(request.prompt)
    proc.stdin.close()

    activity: list[float] = [time.monotonic()]
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    t_out = threading.Thread(
        target=_read_lines,
        args=(proc.stdout, stdout_lines, None, activity),
    )
    t_err = threading.Thread(
        target=_read_lines,
        args=(proc.stderr, stderr_lines, None, activity),
    )
    t_out.start()
    t_err.start()

    return StreamingHandle(proc, stdout_lines, stderr_lines, [t_out, t_err], activity)


def run(
    request: ClaudeRequest,
    *,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ClaudeResult:
    """Invoke Claude and block until completion.

    When on_stdout_line is provided, each stdout line is forwarded
    to the callback as it arrives (for streaming output to terminal).
    """
    cmd = _build_cmd(request)
    env = _build_env()

    proc = subprocess.Popen(
        cmd,
        cwd=request.cwd,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert proc.stdin is not None
    proc.stdin.write(request.prompt)
    proc.stdin.close()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    t_out = threading.Thread(
        target=_read_lines,
        args=(proc.stdout, stdout_lines, on_stdout_line),
    )
    t_err = threading.Thread(
        target=_read_lines,
        args=(proc.stderr, stderr_lines),
    )
    t_out.start()
    t_err.start()

    if request.timeout is not None:
        timer = threading.Timer(request.timeout, proc.terminate)
        timer.start()
    else:
        timer = None

    t_out.join()
    t_err.join()
    returncode = proc.wait()

    if timer is not None:
        timer.cancel()

    return ClaudeResult(
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        returncode=returncode,
    )
