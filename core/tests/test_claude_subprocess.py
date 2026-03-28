"""Tests for core/claude_subprocess.py."""

from __future__ import annotations

import io
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.claude_subprocess import (
    ClaudeRequest,
    ClaudeResult,
    StreamingHandle,
    async_start,
    run,
    start,
)
from core.sandbox import SandboxConfig, SandboxResult


def _fake_popen(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock Popen that yields stdout/stderr line by line."""
    mock = MagicMock()
    mock.stdin = MagicMock()
    mock.stdout = io.StringIO(stdout)
    mock.stderr = io.StringIO(stderr)
    mock.wait.return_value = returncode
    mock.returncode = returncode
    return mock


def test_run_collects_output_and_returns_result() -> None:
    request = ClaudeRequest(
        prompt="Hello Claude",
        cwd="/fake/dir",
        model="claude-sonnet-4-6",
    )
    mock_proc = _fake_popen(stdout="Line 1\nLine 2\n", stderr="warn\n", returncode=0)

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        result = run(request)

    assert isinstance(result, ClaudeResult)
    assert result.stdout == "Line 1\nLine 2\n"
    assert result.stderr == "warn\n"
    assert result.returncode == 0
    assert result.output == "Line 1\nLine 2\nwarn\n"
    # Prompt delivered via stdin
    mock_proc.stdin.write.assert_called_once_with("Hello Claude")
    mock_proc.stdin.close.assert_called_once()


def test_run_strips_claudecode_from_env() -> None:
    request = ClaudeRequest(prompt="x", cwd="/fake", model="m")
    mock_proc = _fake_popen()

    with (
        patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch.dict("os.environ", {"CLAUDECODE": "1", "PATH": "/usr/bin"}, clear=True),
    ):
        run(request)

    env_passed = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env")
    assert "CLAUDECODE" not in env_passed
    assert "PATH" in env_passed


def test_run_wraps_with_nix_when_flake_exists(tmp_path: object) -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        Path(td, "flake.nix").touch()
        request = ClaudeRequest(prompt="x", cwd=td, model="m")
        mock_proc = _fake_popen()

        with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc) as mock_popen:
            run(request)

        cmd = mock_popen.call_args[0][0]
        assert cmd[:3] == ["nix", "develop", "--command"]
        assert "claude" in cmd


def test_run_no_nix_when_no_flake() -> None:
    request = ClaudeRequest(prompt="x", cwd="/nonexistent/dir", model="m")
    mock_proc = _fake_popen()

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc) as mock_popen:
        run(request)

    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "claude"


def test_run_calls_on_stdout_line_callback() -> None:
    request = ClaudeRequest(prompt="x", cwd="/fake", model="m")
    mock_proc = _fake_popen(stdout="line1\nline2\n")

    lines_seen: list[str] = []
    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        run(request, on_stdout_line=lines_seen.append)

    assert lines_seen == ["line1\n", "line2\n"]


def test_run_timeout_terminates_process() -> None:
    request = ClaudeRequest(prompt="x", cwd="/fake", model="m", timeout=0.05)

    mock_proc = _fake_popen()
    # Make wait() block until terminate is called
    terminated = threading.Event()
    mock_proc.terminate.side_effect = lambda: terminated.set()
    mock_proc.wait.side_effect = lambda: (terminated.wait(timeout=2.0), 0)[1]
    # stdout/stderr read fast (empty), but wait() blocks until terminate

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        run(request)

    mock_proc.terminate.assert_called()


def test_start_returns_handle_and_wait_returns_result() -> None:
    request = ClaudeRequest(prompt="hi", cwd="/fake", model="m")
    mock_proc = _fake_popen(stdout="done\n", returncode=0)

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        handle = start(request)

    assert isinstance(handle, StreamingHandle)
    result = handle.wait()
    assert result.stdout == "done\n"
    assert result.returncode == 0


def test_start_get_last_activity_returns_timestamp() -> None:
    request = ClaudeRequest(prompt="hi", cwd="/fake", model="m")
    mock_proc = _fake_popen(stdout="line\n")

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        handle = start(request)

    handle.wait()
    activity = handle.get_last_activity()
    assert isinstance(activity, float)
    assert activity > 0


def test_start_terminate_kills_process() -> None:
    request = ClaudeRequest(prompt="hi", cwd="/fake", model="m")
    mock_proc = _fake_popen()

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc):
        handle = start(request)

    handle.terminate()
    mock_proc.terminate.assert_called_once()


def _make_mock_sandbox(
    returncode: int = 0, stdout: str = "sandbox output\n", stderr: str = ""
) -> MagicMock:
    """Create a mock Sandbox with async start()."""
    mock = MagicMock()
    mock.start = AsyncMock(
        return_value=SandboxResult(returncode=returncode, stdout=stdout, stderr=stderr)
    )
    return mock


def test_run_delegates_to_sandbox_when_set() -> None:
    mock_sandbox = _make_mock_sandbox(stdout="sandbox output\n")
    config = SandboxConfig(env={"FOO": "bar"})
    request = ClaudeRequest(
        prompt="hi",
        cwd="/fake/dir",
        model="m",
        sandbox=mock_sandbox,
        sandbox_config=config,
    )

    result = run(request)

    mock_sandbox.start.assert_called_once()
    call_args = mock_sandbox.start.call_args
    cmd, cfg, cwd = call_args[0]
    assert "claude" in cmd
    assert cfg is config
    assert cwd == "/fake/dir"
    assert result.stdout == "sandbox output\n"
    assert result.returncode == 0


def test_run_uses_subprocess_when_sandbox_is_none() -> None:
    request = ClaudeRequest(prompt="x", cwd="/fake", model="m")
    mock_proc = _fake_popen(stdout="direct output\n")

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc) as mock_popen:
        result = run(request)

    mock_popen.assert_called_once()
    assert result.stdout == "direct output\n"


async def test_async_start_delegates_to_sandbox() -> None:
    mock_sandbox = _make_mock_sandbox(stdout="async sandbox\n", returncode=0)
    config = SandboxConfig(env={"X": "y"})
    request = ClaudeRequest(
        prompt="hi",
        cwd="/fake/dir",
        model="m",
        sandbox=mock_sandbox,
        sandbox_config=config,
    )

    result = await async_start(request)

    mock_sandbox.start.assert_called_once()
    call_args = mock_sandbox.start.call_args
    cmd, cfg, cwd = call_args[0]
    assert "claude" in cmd
    assert cfg is config
    assert cwd == "/fake/dir"
    assert result.stdout == "async sandbox\n"
    assert result.returncode == 0


async def test_async_start_falls_back_to_thread_when_no_sandbox() -> None:
    request = ClaudeRequest(prompt="hi", cwd="/fake", model="m")
    mock_proc = _fake_popen(stdout="thread output\n", returncode=0)

    with patch("core.claude_subprocess.subprocess.Popen", return_value=mock_proc) as mock_popen:
        result = await async_start(request)

    mock_popen.assert_called_once()
    assert result.stdout == "thread output\n"
    assert result.returncode == 0


def test_run_sandbox_receives_nix_wrapped_command() -> None:
    mock_sandbox = _make_mock_sandbox()
    with tempfile.TemporaryDirectory() as td:
        Path(td, "flake.nix").touch()
        request = ClaudeRequest(
            prompt="hi",
            cwd=td,
            model="m",
            sandbox=mock_sandbox,
        )
        run(request)

    call_args = mock_sandbox.start.call_args
    cmd = call_args[0][0]
    assert cmd[:3] == ["nix", "develop", "--command"]


def test_run_sandbox_config_defaults_to_empty() -> None:
    mock_sandbox = _make_mock_sandbox()
    request = ClaudeRequest(
        prompt="hi",
        cwd="/fake",
        model="m",
        sandbox=mock_sandbox,
        sandbox_config=None,
    )

    run(request)

    call_args = mock_sandbox.start.call_args
    cfg = call_args[0][1]
    assert isinstance(cfg, SandboxConfig)
    assert cfg.env == {}
    assert cfg.writable_paths == []
