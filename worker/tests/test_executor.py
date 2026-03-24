"""Tests for worker.executor.CommandExecutor — all 11 command handlers."""

from __future__ import annotations

import base64
import subprocess
from unittest.mock import MagicMock, mock_open, patch
from uuid import uuid4

from core.claude_subprocess import ClaudeResult
from core.remote_protocol import (
    CreateWorktreeRequest,
    GetDiffRequest,
    GetProjectStatusRequest,
    GetStatusRequest,
    ReadFileRequest,
    RemoveWorktreeRequest,
    RunClaudeRequest,
    RunCommandRequest,
    SetupEnvironmentRequest,
    SetupProjectRequest,
    UpdateProjectRequest,
)
from worker.executor import CommandExecutor


def _rid() -> str:
    return str(uuid4())


def _executor(projects: dict[str, str] | None = None) -> CommandExecutor:
    return CommandExecutor("worker-1", projects or {})


# ---------------------------------------------------------------------------
# GetProjectStatus
# ---------------------------------------------------------------------------


class TestHandleGetProjectStatus:
    async def test_returns_not_exists_for_unknown_project(self) -> None:
        ex = _executor()
        req = GetProjectStatusRequest(
            type="get_project_status", request_id=_rid(), project_id="missing"
        )
        resp = await ex.handle(req)
        assert resp.success is True
        assert resp.exists is False  # type: ignore[union-attr]

    async def test_returns_path_and_head_for_known_project(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = GetProjectStatusRequest(
            type="get_project_status", request_id=_rid(), project_id="proj1"
        )
        mock_cp = subprocess.CompletedProcess([], 0, "abc123\n", "")
        with patch("subprocess.run", return_value=mock_cp):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.exists is True  # type: ignore[union-attr]
        assert resp.path == "/repo"  # type: ignore[union-attr]
        assert resp.head_commit == "abc123"  # type: ignore[union-attr]

    async def test_returns_error_when_git_fails(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = GetProjectStatusRequest(
            type="get_project_status", request_id=_rid(), project_id="proj1"
        )
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git rev-parse HEAD"),
        ):
            resp = await ex.handle(req)
        assert resp.success is False
        assert resp.error is not None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# SetupProject
# ---------------------------------------------------------------------------


class TestHandleSetupProject:
    async def test_extracts_bundle_and_registers_project(self) -> None:
        ex = _executor()
        bundle = base64.b64encode(b"fake-bundle").decode()
        req = SetupProjectRequest(
            type="setup_project",
            request_id=_rid(),
            project_id="proj1",
            bundle_b64=bundle,
            path="/tmp/proj1",
        )
        with patch("core.git_transfer.extract_bundle") as mock_extract:
            resp = await ex.handle(req)
        mock_extract.assert_called_once_with(b"fake-bundle", "/tmp/proj1")
        assert resp.success is True
        assert ex._projects["proj1"] == "/tmp/proj1"

    async def test_returns_error_on_extract_failure(self) -> None:
        ex = _executor()
        req = SetupProjectRequest(
            type="setup_project",
            request_id=_rid(),
            project_id="proj1",
            bundle_b64=base64.b64encode(b"bad").decode(),
            path="/tmp/proj1",
        )
        with patch(
            "core.git_transfer.extract_bundle", side_effect=RuntimeError("bundle bad")
        ):
            resp = await ex.handle(req)
        assert resp.success is False
        assert "bundle bad" in (resp.error or "")


# ---------------------------------------------------------------------------
# UpdateProject
# ---------------------------------------------------------------------------


class TestHandleUpdateProject:
    async def test_applies_bundle_to_project(self) -> None:
        ex = _executor({"proj1": "/repo"})
        bundle_data = b"\x00binary-bundle-data"
        req = UpdateProjectRequest(
            type="update_project",
            request_id=_rid(),
            project_id="proj1",
            patch_b64=base64.b64encode(bundle_data).decode(),
        )
        with (
            patch("subprocess.run") as mock_run,
            patch("os.unlink"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            resp = await ex.handle(req)
        assert resp.success is True
        # Should have called git fetch and git reset
        assert mock_run.call_count >= 2

    async def test_returns_error_on_fetch_failure(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = UpdateProjectRequest(
            type="update_project",
            request_id=_rid(),
            project_id="proj1",
            patch_b64=base64.b64encode(b"\x00bad").decode(),
        )
        with (
            patch("subprocess.run") as mock_run,
            patch("os.unlink"),
        ):
            mock_run.return_value = MagicMock(
                returncode=1, stderr="fatal: not a bundle"
            )
            resp = await ex.handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# CreateWorktree
# ---------------------------------------------------------------------------


class TestHandleCreateWorktree:
    async def test_creates_worktree_and_sets_execution_id(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = CreateWorktreeRequest(
            type="create_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            base_commit="abc123",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch("subprocess.run", return_value=mock_cp),
            patch("os.makedirs"),
        ):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.worktree_path == "/repo/.worktrees/exec-1"  # type: ignore[union-attr]
        assert ex._current_execution_id == "exec-1"

    async def test_returns_error_on_git_failure(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = CreateWorktreeRequest(
            type="create_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            base_commit="badref",
        )
        mock_cp = subprocess.CompletedProcess([], 1, "", "fatal: not a valid ref")
        with (
            patch("subprocess.run", return_value=mock_cp),
            patch("os.makedirs"),
        ):
            resp = await ex.handle(req)
        assert resp.success is False
        assert "fatal" in (resp.error or "")


# ---------------------------------------------------------------------------
# RemoveWorktree
# ---------------------------------------------------------------------------


class TestHandleRemoveWorktree:
    async def test_removes_worktree_and_clears_execution_id(self) -> None:
        ex = _executor({"proj1": "/repo"})
        ex._current_execution_id = "exec-1"
        req = RemoveWorktreeRequest(
            type="remove_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "", "")
        with patch("subprocess.run", return_value=mock_cp):
            resp = await ex.handle(req)
        assert resp.success is True
        assert ex._current_execution_id is None

    async def test_preserves_other_execution_id(self) -> None:
        ex = _executor({"proj1": "/repo"})
        ex._current_execution_id = "exec-2"
        req = RemoveWorktreeRequest(
            type="remove_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "", "")
        with patch("subprocess.run", return_value=mock_cp):
            await ex.handle(req)
        assert ex._current_execution_id == "exec-2"

    async def test_returns_error_on_git_failure(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = RemoveWorktreeRequest(
            type="remove_worktree",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        mock_cp = subprocess.CompletedProcess([], 1, "", "fatal: no such worktree")
        with patch("subprocess.run", return_value=mock_cp):
            resp = await ex.handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# GetDiff
# ---------------------------------------------------------------------------


class TestHandleGetDiff:
    async def test_returns_patch_from_worktree(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = GetDiffRequest(
            type="get_diff",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        with patch("core.git_transfer.create_patch", return_value="--- a\n+++ b\n"):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.patch == "--- a\n+++ b\n"  # type: ignore[union-attr]

    async def test_returns_error_on_failure(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = GetDiffRequest(
            type="get_diff",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
        )
        with patch(
            "core.git_transfer.create_patch", side_effect=RuntimeError("git error")
        ):
            resp = await ex.handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# RunClaude
# ---------------------------------------------------------------------------


class TestHandleRunClaude:
    async def test_invokes_claude_and_returns_result(self) -> None:
        ex = _executor()
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="do the thing",
            model="claude-opus-4-5",
            tools=["Bash", "Read"],
            cwd="/repo/.worktrees/exec-1",
        )
        mock_result = ClaudeResult(stdout="COMPLETED", stderr="", returncode=0)
        with patch("core.claude_subprocess.run", return_value=mock_result):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.status == "completed"  # type: ignore[union-attr]
        assert resp.stdout == "COMPLETED"  # type: ignore[union-attr]
        assert resp.returncode == 0  # type: ignore[union-attr]
        assert ex._current_execution_id == "exec-1"

    async def test_returns_failed_status_on_nonzero_returncode(self) -> None:
        ex = _executor()
        req = RunClaudeRequest(
            type="run_claude",
            request_id=_rid(),
            execution_id="exec-1",
            prompt="do the thing",
            model="claude-opus-4-5",
            tools=[],
            cwd="/repo",
        )
        mock_result = ClaudeResult(stdout="BLOCKED: reason", stderr="", returncode=1)
        with patch("core.claude_subprocess.run", return_value=mock_result):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.status == "failed"  # type: ignore[union-attr]
        assert resp.returncode == 1  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# RunCommand
# ---------------------------------------------------------------------------


class TestHandleRunCommand:
    async def test_runs_command_and_returns_output(self) -> None:
        ex = _executor()
        req = RunCommandRequest(
            type="run_command",
            request_id=_rid(),
            execution_id="exec-1",
            cmd=["echo", "hello"],
            cwd="/tmp",
        )
        mock_cp = subprocess.CompletedProcess([], 0, "hello\n", "")
        with patch("subprocess.run", return_value=mock_cp):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.stdout == "hello\n"  # type: ignore[union-attr]
        assert resp.returncode == 0  # type: ignore[union-attr]

    async def test_returns_error_on_timeout(self) -> None:
        ex = _executor()
        req = RunCommandRequest(
            type="run_command",
            request_id=_rid(),
            execution_id="exec-1",
            cmd=["sleep", "999"],
            cwd="/tmp",
        )
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["sleep", "999"], 300),
        ):
            resp = await ex.handle(req)
        assert resp.success is False
        assert resp.error == "command timed out"

    async def test_returns_error_on_exception(self) -> None:
        ex = _executor()
        req = RunCommandRequest(
            type="run_command",
            request_id=_rid(),
            execution_id="exec-1",
            cmd=["bad"],
            cwd="/tmp",
        )
        with patch("subprocess.run", side_effect=FileNotFoundError("bad: not found")):
            resp = await ex.handle(req)
        assert resp.success is False
        assert resp.error is not None


# ---------------------------------------------------------------------------
# ReadFile
# ---------------------------------------------------------------------------


class TestHandleReadFile:
    async def test_reads_file_content(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = ReadFileRequest(
            type="read_file",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            path="README.md",
        )
        with patch("builtins.open", mock_open(read_data="# Hello")):
            resp = await ex.handle(req)
        assert resp.success is True
        assert resp.content == "# Hello"  # type: ignore[union-attr]

    async def test_returns_error_when_file_not_found(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = ReadFileRequest(
            type="read_file",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            path="missing.txt",
        )
        with patch("builtins.open", side_effect=FileNotFoundError):
            resp = await ex.handle(req)
        assert resp.success is False
        assert resp.error == "file not found"


# ---------------------------------------------------------------------------
# SetupEnvironment
# ---------------------------------------------------------------------------


class TestHandleSetupEnvironment:
    async def test_creates_symlinks_for_each_entry(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = SetupEnvironmentRequest(
            type="setup_environment",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            symlinks=[".venv"],
        )
        with patch("worker.executor.safe_symlink") as mock_symlink, \
             patch("os.makedirs"):
            resp = await ex.handle(req)
        mock_symlink.assert_called_once_with(
            "/repo/.venv", "/repo/.worktrees/exec-1/.venv"
        )
        assert resp.success is True

    async def test_returns_error_on_symlink_failure(self) -> None:
        ex = _executor({"proj1": "/repo"})
        req = SetupEnvironmentRequest(
            type="setup_environment",
            request_id=_rid(),
            project_id="proj1",
            execution_id="exec-1",
            symlinks=[".venv"],
        )
        with patch(
            "worker.executor.safe_symlink", side_effect=OSError("permission denied")
        ), patch("os.makedirs"):
            resp = await ex.handle(req)
        assert resp.success is False
        assert "permission denied" in (resp.error or "")


# ---------------------------------------------------------------------------
# GetStatus
# ---------------------------------------------------------------------------


class TestHandleGetStatus:
    async def test_returns_none_when_no_execution(self) -> None:
        ex = _executor()
        req = GetStatusRequest(type="get_status", request_id=_rid())
        resp = await ex.handle(req)
        assert resp.success is True
        assert resp.current_execution_id is None  # type: ignore[union-attr]

    async def test_returns_current_execution_id(self) -> None:
        ex = _executor()
        ex._current_execution_id = "exec-42"
        req = GetStatusRequest(type="get_status", request_id=_rid())
        resp = await ex.handle(req)
        assert resp.current_execution_id == "exec-42"  # type: ignore[union-attr]
