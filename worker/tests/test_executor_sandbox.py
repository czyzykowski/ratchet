"""Tests for sandbox integration in worker.executor.CommandExecutor."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.remote_protocol import (
    RemoveWorktreeRequest,
    RunClaudeRequest,
)
from core.sandbox import NullSandbox
from worker.executor import CommandExecutor


def _rid() -> str:
    return str(uuid4())


def _executor(
    sandbox_name: str = "none", projects: dict[str, str] | None = None
) -> CommandExecutor:
    return CommandExecutor("worker-1", projects or {}, sandbox_name=sandbox_name)


# ---------------------------------------------------------------------------
# Sandbox resolution
# ---------------------------------------------------------------------------


class TestSandboxResolution:
    def test_sandbox_none_uses_null_sandbox(self) -> None:
        executor = _executor(sandbox_name="none")
        assert isinstance(executor._sandbox, NullSandbox)

    def test_sandbox_auto_resolves_via_registry(self) -> None:
        null = NullSandbox()
        with patch(
            "core.sandbox.default_registry.auto_detect",
            new=AsyncMock(return_value=null),
        ):
            executor = _executor(sandbox_name="auto")
        assert executor._sandbox is null

    def test_sandbox_name_looked_up_from_registry(self) -> None:
        null = NullSandbox()
        with patch("core.sandbox.default_registry.get", return_value=null) as mock_get:
            executor = _executor(sandbox_name="null")
        mock_get.assert_called_once_with("null")
        assert executor._sandbox is null


# ---------------------------------------------------------------------------
# ClaudeRequest gets sandbox fields
# ---------------------------------------------------------------------------


class TestSandboxPassedToClaudeRequest:
    async def test_sandbox_and_config_set_on_claude_request(self) -> None:
        from core.claude_subprocess import ClaudeResult

        executor = _executor(sandbox_name="none")
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="do the thing",
            model="claude-opus-4-5",
            tools=["Bash"],
            cwd="/repo/.worktrees/exec-1",
        )
        mock_result = ClaudeResult(stdout="COMPLETED", stderr="", returncode=0)
        captured: list[object] = []

        async def fake_async_start(request):  # type: ignore[no-untyped-def]
            captured.append(request)
            return mock_result

        with patch("core.claude_subprocess.async_start", new=fake_async_start):
            await executor.handle(req)

        assert len(captured) == 1
        claude_req = captured[0]
        assert claude_req.sandbox is executor._sandbox  # type: ignore[union-attr]
        assert claude_req.sandbox_config is not None  # type: ignore[union-attr]

    async def test_project_path_extracted_from_worktree_cwd(self) -> None:
        from core.claude_subprocess import ClaudeResult
        from core.sandbox import build_sandbox_config

        executor = _executor(sandbox_name="none")
        cwd = "/some/project/.worktrees/exec-42"
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-42",
            prompt="work",
            model="claude-opus-4-5",
            tools=[],
            cwd=cwd,
        )
        mock_result = ClaudeResult(stdout="", stderr="", returncode=0)
        captured: list[object] = []

        async def fake_async_start(request):  # type: ignore[no-untyped-def]
            captured.append(request)
            return mock_result

        with patch("core.claude_subprocess.async_start", new=fake_async_start):
            await executor.handle(req)

        claude_req = captured[0]
        expected_config = build_sandbox_config(
            worktree_path=cwd,
            project_path="/some/project",
            symlinked_dirs=[".venv", "node_modules", ".env", ".deno"],
        )
        assert claude_req.sandbox_config.writable_paths == expected_config.writable_paths  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Sandbox start failure
# ---------------------------------------------------------------------------


class TestSandboxStartFailure:
    async def test_start_failure_returns_nonzero_run_claude_response(self) -> None:
        executor = _executor(sandbox_name="none")
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="do the thing",
            model="claude-opus-4-5",
            tools=[],
            cwd="/repo/.worktrees/exec-1",
        )
        with patch(
            "core.claude_subprocess.async_start",
            new=AsyncMock(side_effect=RuntimeError("sandbox start error")),
        ):
            resp = await executor.handle(req)

        assert resp.success is True  # type: ignore[union-attr]
        assert resp.returncode == 1  # type: ignore[union-attr]
        assert "sandbox start error" in (resp.stderr or "")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Session JSONL ephemeral path
# ---------------------------------------------------------------------------


class TestSessionJsonlEphemeralPath:
    async def test_reads_session_jsonl_from_ephemeral_path(self, tmp_path: Path) -> None:
        import os

        from core.claude_subprocess import ClaudeResult

        # Set up fake ephemeral .claude directory
        ephemeral_dir = tmp_path / "ephemeral_claude"
        cwd = "/repo/.worktrees/exec-1"
        slug = os.path.abspath(cwd).replace("/", "-").replace(".", "-")
        project_dir = ephemeral_dir / "projects" / slug
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / "session.jsonl"
        jsonl_file.write_text('{"type":"assistant","content":"from ephemeral"}\n')

        # Create a mock sandbox that returns the ephemeral path
        mock_sandbox = MagicMock(spec=NullSandbox)
        mock_sandbox.name.return_value = "mock"
        mock_sandbox.get_ephemeral_path.return_value = str(ephemeral_dir)
        mock_sandbox.cleanup = AsyncMock()

        executor = CommandExecutor("worker-1", {}, sandbox_name="none")
        executor._sandbox = mock_sandbox

        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="work",
            model="claude-opus-4-5",
            tools=[],
            cwd=cwd,
        )
        mock_result = ClaudeResult(stdout="COMPLETED", stderr="", returncode=0)
        with patch("core.claude_subprocess.async_start", new=AsyncMock(return_value=mock_result)):
            resp = await executor.handle(req)

        assert resp.success is True  # type: ignore[union-attr]
        assert resp.session_jsonl is not None  # type: ignore[union-attr]
        assert "from ephemeral" in (resp.session_jsonl or "")  # type: ignore[union-attr]
        mock_sandbox.get_ephemeral_path.assert_called_with("~/.claude")

    async def test_falls_back_to_standard_path_when_ephemeral_returns_none(
        self, tmp_path: Path
    ) -> None:
        from core.claude_subprocess import ClaudeResult

        executor = _executor(sandbox_name="none")  # NullSandbox returns None for ephemeral
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="work",
            model="claude-opus-4-5",
            tools=[],
            cwd="/repo/.worktrees/exec-1",
        )
        mock_result = ClaudeResult(stdout="COMPLETED", stderr="", returncode=0)
        called_with: list[str] = []

        @staticmethod  # type: ignore[misc]
        def capturing_read(cwd: str) -> str | None:
            called_with.append(cwd)
            return None

        with (
            patch("core.claude_subprocess.async_start", new=AsyncMock(return_value=mock_result)),
            patch.object(CommandExecutor, "_read_session_jsonl", capturing_read),
        ):
            resp = await executor.handle(req)

        assert resp.success is True  # type: ignore[union-attr]
        assert called_with == ["/repo/.worktrees/exec-1"]


# ---------------------------------------------------------------------------
# Sandbox cleanup on worktree removal
# ---------------------------------------------------------------------------


class TestSandboxCleanupOnWorktreeRemoval:
    async def test_cleanup_called_on_worktree_removal(self) -> None:
        mock_sandbox = MagicMock(spec=NullSandbox)
        mock_sandbox.name.return_value = "mock"
        mock_sandbox.cleanup = AsyncMock()

        executor = CommandExecutor("worker-1", {"proj1": "/repo"}, sandbox_name="none")
        executor._sandbox = mock_sandbox
        executor._current_execution_id = "exec-1"

        req = RemoveWorktreeRequest(
            type="remove_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "", "")
        with patch("subprocess.run", return_value=mock_cp):
            resp = await executor.handle(req)

        assert resp.success is True  # type: ignore[union-attr]
        mock_sandbox.cleanup.assert_awaited_once()

    async def test_cleanup_failure_is_logged_but_response_still_succeeds(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        mock_sandbox = MagicMock(spec=NullSandbox)
        mock_sandbox.name.return_value = "mock"
        mock_sandbox.cleanup = AsyncMock(side_effect=RuntimeError("cleanup exploded"))

        executor = CommandExecutor("worker-1", {"proj1": "/repo"}, sandbox_name="none")
        executor._sandbox = mock_sandbox
        executor._current_execution_id = "exec-1"

        req = RemoveWorktreeRequest(
            type="remove_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch("subprocess.run", return_value=mock_cp),
            caplog.at_level(logging.WARNING, logger="worker.executor"),
        ):
            resp = await executor.handle(req)

        assert resp.success is True  # type: ignore[union-attr]
        assert any("cleanup" in r.message.lower() for r in caplog.records)
