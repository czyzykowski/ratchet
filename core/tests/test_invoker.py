"""Unit tests for core/invoker.py — subprocess.Popen is mocked throughout."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from core.context_assembler import ExecutionContext
from core.invoker import (
    ClaudeCodeInvoker,
    InvocationResult,
    _watchdog_loop,
    get_traces_dir,
    parse_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(worktree_path: str = "/tmp/worktree") -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        task_id=uuid4(),
        spec_id=uuid4(),
        worktree_path=worktree_path,
        prompt="do the thing",
    )


def _fake_popen(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Return a mock Popen-like object with iterable stdout/stderr."""
    mock = MagicMock()
    mock.stdout = iter(stdout.splitlines(keepends=True))
    mock.stderr = iter(stderr.splitlines(keepends=True))
    mock.wait.return_value = returncode
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# parse_output
# ---------------------------------------------------------------------------

class TestParseOutput:
    def test_completed_marker(self):
        status, reason = parse_output("some text\nCOMPLETED: all done\nmore text", 0)
        assert status == "completed"
        assert reason is None

    def test_completed_marker_nonzero_exit(self):
        # COMPLETED takes priority even if exit code is non-zero
        status, reason = parse_output("COMPLETED: spec done", 1)
        assert status == "completed"
        assert reason is None

    def test_blocked_marker_with_blank_line(self):
        output = "some preamble\nBLOCKED: missing env var\ndetails here\n\nmore stuff"
        status, reason = parse_output(output, 0)
        assert status == "failed"
        assert "missing env var" in reason
        assert "more stuff" not in reason

    def test_blocked_marker_no_blank_line(self):
        output = "BLOCKED: no database user\nstill part of block"
        status, reason = parse_output(output, 0)
        assert status == "failed"
        assert "no database user" in reason

    def test_crashed_nonzero_no_marker(self):
        # 11 lines: last 10 are line2..line11; line1 alone should not appear
        output = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\nline11"
        status, reason = parse_output(output, 1)
        assert status == "crashed"
        assert "code 1" in reason
        assert "line11" in reason
        # "line1\n" (i.e. the first line) should not be in the last-10 excerpt
        last_output_section = reason.split("Last output:\n", 1)[1]
        assert not last_output_section.startswith("line1\n")

    def test_crashed_includes_exit_code(self):
        status, reason = parse_output("something went wrong", 127)
        assert status == "crashed"
        assert "127" in reason

    def test_no_marker_exit_zero(self):
        status, reason = parse_output("Claude said hello but nothing else", 0)
        assert status == "failed"
        assert reason == "no completion marker found in output"

    def test_empty_output_nonzero(self):
        status, reason = parse_output("", 1)
        assert status == "crashed"
        assert "code 1" in reason


# ---------------------------------------------------------------------------
# get_traces_dir
# ---------------------------------------------------------------------------

class TestGetTracesDir:
    def test_uses_ratchet_traces_dir_env(self, tmp_path):
        custom = str(tmp_path / "custom_traces")
        with patch.dict(os.environ, {"RATCHET_TRACES_DIR": custom}, clear=False):
            result = get_traces_dir()
        assert result == str(Path(custom).resolve())
        assert Path(result).is_dir()

    def test_default_uses_xdg_data_home(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "RATCHET_TRACES_DIR"}
        env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
        with patch.dict(os.environ, env, clear=True):
            result = get_traces_dir()
        assert result.endswith("ratchet/traces")
        assert Path(result).is_dir()

    def test_default_without_xdg(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RATCHET_TRACES_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        # Patch Path.home() to avoid writing to real home
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch("core.invoker.Path.home", return_value=fake_home):
            result = get_traces_dir()
        assert "ratchet/traces" in result
        assert Path(result).is_dir()

    def test_creates_directory_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "new" / "nested" / "traces")
        with patch.dict(os.environ, {"RATCHET_TRACES_DIR": new_dir}, clear=False):
            result = get_traces_dir()
        assert Path(result).is_dir()


# ---------------------------------------------------------------------------
# ClaudeCodeInvoker
# ---------------------------------------------------------------------------

class TestClaudeCodeInvoker:
    def test_invoke_completed(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch(
            "subprocess.Popen",
            return_value=_fake_popen(stdout="COMPLETED: all tasks done"),
        ):
            result = invoker.invoke(ctx)

        assert isinstance(result, InvocationResult)
        assert result.status == "completed"
        assert result.failure_reason is None
        assert result.execution_id == ctx.execution_id

    def test_invoke_blocked_failed(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))
        output = "BLOCKED: missing TEST_DATABASE_URL\n\nfurther info"

        with patch("subprocess.Popen", return_value=_fake_popen(stdout=output)):
            result = invoker.invoke(ctx)

        assert result.status == "failed"
        assert "missing TEST_DATABASE_URL" in result.failure_reason

    def test_invoke_crashed(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch(
            "subprocess.Popen",
            return_value=_fake_popen(stdout="fatal error", returncode=1),
        ):
            result = invoker.invoke(ctx)

        assert result.status == "crashed"
        assert "code 1" in result.failure_reason

    def test_invoke_no_marker_exit_zero(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="I did some stuff")):
            result = invoker.invoke(ctx)

        assert result.status == "failed"
        assert result.failure_reason == "no completion marker found in output"

    def test_trace_file_written(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="COMPLETED: done")):
            result = invoker.invoke(ctx)

        trace = Path(result.trace_path)
        assert trace.exists()
        content = trace.read_text()
        assert str(ctx.execution_id) in content
        assert "COMPLETED: done" in content

    def test_trace_file_path_matches_result(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="COMPLETED: ok")):
            result = invoker.invoke(ctx)

        expected_path = tmp_path / f"{ctx.execution_id}.md"
        assert result.trace_path == str(expected_path.resolve())

    def test_trace_file_written_on_crash(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch(
            "subprocess.Popen",
            return_value=_fake_popen(stdout="boom", returncode=2),
        ):
            result = invoker.invoke(ctx)

        assert Path(result.trace_path).exists()

    def test_trace_file_written_on_blocked(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="BLOCKED: something")):
            result = invoker.invoke(ctx)

        assert Path(result.trace_path).exists()

    def test_trace_contains_header_fields(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="COMPLETED: done")):
            result = invoker.invoke(ctx)

        content = Path(result.trace_path).read_text()
        assert f"# Execution Trace: {ctx.execution_id}" in content
        assert f"# Task: {ctx.task_id}" in content
        assert f"# Spec: {ctx.spec_id}" in content
        assert "# Started:" in content

    def test_trace_contains_stderr(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch(
            "subprocess.Popen",
            return_value=_fake_popen(stdout="COMPLETED: ok", stderr="warning: something"),
        ):
            result = invoker.invoke(ctx)

        content = Path(result.trace_path).read_text()
        assert "warning: something" in content

    def test_execution_id_in_result(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch("subprocess.Popen", return_value=_fake_popen(stdout="COMPLETED: done")):
            result = invoker.invoke(ctx)

        assert result.execution_id == ctx.execution_id

    def test_subprocess_called_with_correct_args(self, tmp_path):
        ctx = _make_context(worktree_path=str(tmp_path))
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))

        with patch(
            "subprocess.Popen",
            return_value=_fake_popen(stdout="COMPLETED: ok"),
        ) as mock_popen:
            invoker.invoke(ctx)

        call_args = mock_popen.call_args
        cmd = call_args.args[0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert ctx.prompt not in cmd
        assert "--allowedTools" in cmd
        assert call_args.kwargs["cwd"] == ctx.worktree_path
        assert call_args.kwargs["stdin"] == subprocess.PIPE
        assert call_args.kwargs["stdout"] == subprocess.PIPE
        assert call_args.kwargs["stderr"] == subprocess.PIPE
        assert call_args.kwargs["text"] is True

    def test_traces_dir_created_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "nested" / "traces")
        ClaudeCodeInvoker(traces_dir=new_dir)
        assert Path(new_dir).is_dir()

    def test_uses_get_traces_dir_when_none(self, tmp_path):
        custom = str(tmp_path / "auto_traces")
        with patch.dict(os.environ, {"RATCHET_TRACES_DIR": custom}, clear=False):
            invoker = ClaudeCodeInvoker(traces_dir=None)
        assert invoker.traces_dir == str(Path(custom).resolve())

    def test_failure_reason_from_blocked_report(self, tmp_path):
        ctx = _make_context()
        invoker = ClaudeCodeInvoker(traces_dir=str(tmp_path))
        output = (
            "Starting execution\n"
            "BLOCKED: missing TEST_DATABASE_URL\n"
            "\n"
            "User action required:\n"
            "Set TEST_DATABASE_URL in .env\n"
        )

        with patch("subprocess.Popen", return_value=_fake_popen(stdout=output)):
            result = invoker.invoke(ctx)

        assert result.status == "failed"
        assert "missing TEST_DATABASE_URL" in result.failure_reason
        # Should not include content after blank line
        assert "User action required" not in result.failure_reason


# ---------------------------------------------------------------------------
# TestWatchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_warning_fires_after_threshold(self, capsys):
        """Watchdog prints warning when silent_for exceeds threshold."""
        stop = threading.Event()
        threshold = 60
        base = 100.0

        stop.wait = MagicMock(side_effect=[False, True])

        with patch("time.monotonic", return_value=base + threshold + 1):
            _watchdog_loop(uuid4(), lambda: base, stop, threshold, _interval=0)

        captured = capsys.readouterr()
        assert "[watchdog]" in captured.err
        assert f"silent for {threshold + 1}s" in captured.err
        assert f"(threshold {threshold}s)" in captured.err

    def test_warning_repeats_at_each_threshold_interval(self, capsys):
        """Watchdog re-warns after another threshold period of silence."""
        stop = threading.Event()
        threshold = 60
        base = 100.0
        old_activity = base

        stop.wait = MagicMock(side_effect=[False, False, True])

        with patch("time.monotonic") as mock_mono:
            mock_mono.side_effect = [
                base + threshold + 1,       # iter 1: warn, last_warned set
                base + threshold * 2 + 2,   # iter 2: now - last_warned >= threshold → warn
            ]
            _watchdog_loop(
                uuid4(), lambda: old_activity, stop, threshold, _interval=0
            )

        captured = capsys.readouterr()
        assert captured.err.count("[watchdog]") == 2

    def test_no_warning_when_output_is_active(self, capsys):
        """Watchdog does not warn when activity is recent."""
        stop = threading.Event()
        threshold = 60
        base = 100.0

        stop.wait = MagicMock(side_effect=[False, True])

        # now - last_activity = 10s, below threshold
        with patch("time.monotonic", return_value=base + 10):
            _watchdog_loop(uuid4(), lambda: base, stop, threshold, _interval=0)

        captured = capsys.readouterr()
        assert "[watchdog]" not in captured.err

    def test_watchdog_thread_stops_after_process_exits(self):
        """Watchdog daemon thread exits promptly when stop event is set."""
        stop = threading.Event()

        def get_activity() -> float:
            return time.monotonic()

        t = threading.Thread(
            target=_watchdog_loop,
            args=(uuid4(), get_activity, stop, 300),
            kwargs={"_interval": 1},
            daemon=True,
        )
        t.start()
        stop.set()
        t.join(timeout=5)
        assert not t.is_alive()
