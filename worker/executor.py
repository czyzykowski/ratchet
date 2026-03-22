"""Stateless command executor for remote worker protocol."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess

from core import claude_subprocess, git_transfer
from core.claude_subprocess import ClaudeRequest
from core.remote_protocol import (
    AnyCommandRequest,
    AnyCommandResponse,
    CreateWorktreeRequest,
    CreateWorktreeResponse,
    GetDiffRequest,
    GetDiffResponse,
    GetProjectStatusRequest,
    GetProjectStatusResponse,
    GetStatusRequest,
    GetStatusResponse,
    ReadFileRequest,
    ReadFileResponse,
    RemoveWorktreeRequest,
    RemoveWorktreeResponse,
    RunClaudeRequest,
    RunClaudeResponse,
    RunCommandRequest,
    RunCommandResponse,
    SetupEnvironmentRequest,
    SetupEnvironmentResponse,
    SetupProjectRequest,
    SetupProjectResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
)
from worker.worktree import safe_symlink

logger = logging.getLogger(__name__)

_DEFAULT_CMD_TIMEOUT = 300


class CommandExecutor:
    """Handles command protocol requests from the orchestrator.

    Stateless per execution: only tracks current_execution_id and the
    projects dict. Each handler is independently testable.
    """

    def __init__(self, worker_id: str, projects: dict[str, str], workspace: str = "") -> None:
        self._worker_id = worker_id
        self._projects: dict[str, str] = dict(projects)
        self._workspace = workspace
        self._current_execution_id: str | None = None

    async def handle(self, request: AnyCommandRequest) -> AnyCommandResponse:
        """Dispatch a command request to the appropriate handler."""
        if isinstance(request, GetProjectStatusRequest):
            return self._handle_get_project_status(request)
        elif isinstance(request, SetupProjectRequest):
            return self._handle_setup_project(request)
        elif isinstance(request, UpdateProjectRequest):
            return self._handle_update_project(request)
        elif isinstance(request, CreateWorktreeRequest):
            return self._handle_create_worktree(request)
        elif isinstance(request, RemoveWorktreeRequest):
            return self._handle_remove_worktree(request)
        elif isinstance(request, GetDiffRequest):
            return self._handle_get_diff(request)
        elif isinstance(request, RunClaudeRequest):
            return await self._handle_run_claude(request)
        elif isinstance(request, RunCommandRequest):
            return self._handle_run_command(request)
        elif isinstance(request, ReadFileRequest):
            return self._handle_read_file(request)
        elif isinstance(request, SetupEnvironmentRequest):
            return self._handle_setup_environment(request)
        elif isinstance(request, GetStatusRequest):
            return self._handle_get_status(request)
        else:
            raise ValueError(f"Unknown request type: {request.type}")

    def _worktree_path(self, project_id: str, execution_id: str) -> str:
        project_path = self._projects[project_id]
        return os.path.join(project_path, ".worktrees", execution_id)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_get_project_status(
        self, request: GetProjectStatusRequest
    ) -> GetProjectStatusResponse:
        if request.project_id not in self._projects:
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=request.request_id,
                success=True,
                exists=False,
            )
        path = self._projects[request.project_id]
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
            )
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=request.request_id,
                success=True,
                exists=True,
                path=path,
                head_commit=result.stdout.strip(),
            )
        except Exception as exc:
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=request.request_id,
                success=False,
                exists=True,
                path=path,
                error=str(exc),
            )

    def _handle_setup_project(
        self, request: SetupProjectRequest
    ) -> SetupProjectResponse:
        try:
            # Resolve path: use workspace root if set, otherwise expand as-is
            if self._workspace:
                path = os.path.join(self._workspace, request.path)
            else:
                path = os.path.expanduser(request.path)
            path = os.path.abspath(path)
            # Remove stale directory from a previous failed setup
            if os.path.exists(path):
                import shutil

                shutil.rmtree(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            bundle_bytes = base64.b64decode(request.bundle_b64)
            git_transfer.extract_bundle(bundle_bytes, path)
            self._projects[request.project_id] = path
            return SetupProjectResponse(
                type="setup_project_response",
                request_id=request.request_id,
                success=True,
            )
        except Exception as exc:
            return SetupProjectResponse(
                type="setup_project_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_update_project(
        self, request: UpdateProjectRequest
    ) -> UpdateProjectResponse:
        try:
            path = self._projects[request.project_id]
            patch = base64.b64decode(request.patch_b64).decode()
            git_transfer.apply_patch(path, patch)
            return UpdateProjectResponse(
                type="update_project_response",
                request_id=request.request_id,
                success=True,
            )
        except Exception as exc:
            return UpdateProjectResponse(
                type="update_project_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_create_worktree(
        self, request: CreateWorktreeRequest
    ) -> CreateWorktreeResponse:
        try:
            project_path = self._projects[request.project_id]
            worktree_path = os.path.join(
                project_path, ".worktrees", request.execution_id
            )
            branch_name = f"execution/{request.execution_id}"
            os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
            # Resolve base_commit: if it's a branch/ref that exists, use it.
            # If not, fall back to HEAD.
            resolve = subprocess.run(
                ["git", "rev-parse", "--verify", request.base_commit],
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            base = resolve.stdout.strip() if resolve.returncode == 0 else "HEAD"
            logger.info(
                "create_worktree: project=%s branch=%s base=%s (resolved from %s) path=%s",
                request.project_id[:8],
                branch_name,
                base[:12],
                request.base_commit[:20],
                worktree_path,
            )
            # Try creating a new branch from base first.
            # If the branch already exists (e.g. QA re-run), check it out directly.
            result = subprocess.run(
                [
                    "git", "worktree", "add", worktree_path,
                    "-b", branch_name, base,
                ],
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "already exists" in result.stderr:
                # Branch exists — check out directly
                result = subprocess.run(
                    ["git", "worktree", "add", worktree_path, branch_name],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                )
            if result.returncode != 0:
                return CreateWorktreeResponse(
                    type="create_worktree_response",
                    request_id=request.request_id,
                    success=False,
                    error=result.stderr.strip(),
                )
            self._current_execution_id = request.execution_id
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=request.request_id,
                success=True,
                worktree_path=worktree_path,
            )
        except Exception as exc:
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_remove_worktree(
        self, request: RemoveWorktreeRequest
    ) -> RemoveWorktreeResponse:
        try:
            project_path = self._projects[request.project_id]
            worktree_path = os.path.join(
                project_path, ".worktrees", request.execution_id
            )
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return RemoveWorktreeResponse(
                    type="remove_worktree_response",
                    request_id=request.request_id,
                    success=False,
                    error=result.stderr.strip(),
                )
            if self._current_execution_id == request.execution_id:
                self._current_execution_id = None
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=request.request_id,
                success=True,
            )
        except Exception as exc:
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_get_diff(self, request: GetDiffRequest) -> GetDiffResponse:
        try:
            worktree_path = self._worktree_path(
                request.project_id, request.execution_id
            )
            patch = git_transfer.create_patch(worktree_path)
            return GetDiffResponse(
                type="get_diff_response",
                request_id=request.request_id,
                success=True,
                patch=patch,
            )
        except Exception as exc:
            return GetDiffResponse(
                type="get_diff_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    async def _handle_run_claude(self, request: RunClaudeRequest) -> RunClaudeResponse:
        try:
            claude_request = ClaudeRequest(
                prompt=request.prompt,
                cwd=request.cwd,
                model=request.model,
                allowed_tools=",".join(request.tools),
            )
            self._current_execution_id = request.execution_id
            result = await asyncio.to_thread(claude_subprocess.run, claude_request)
            status = "completed" if result.returncode == 0 else "failed"
            return RunClaudeResponse(
                type="run_claude_response",
                request_id=request.request_id,
                success=True,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                status=status,
            )
        except Exception as exc:
            return RunClaudeResponse(
                type="run_claude_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_run_command(self, request: RunCommandRequest) -> RunCommandResponse:
        try:
            result = subprocess.run(
                request.cmd,
                cwd=request.cwd,
                capture_output=True,
                text=True,
                timeout=_DEFAULT_CMD_TIMEOUT,
            )
            return RunCommandResponse(
                type="run_command_response",
                request_id=request.request_id,
                success=True,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return RunCommandResponse(
                type="run_command_response",
                request_id=request.request_id,
                success=False,
                error="command timed out",
            )
        except Exception as exc:
            return RunCommandResponse(
                type="run_command_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_read_file(self, request: ReadFileRequest) -> ReadFileResponse:
        try:
            worktree_path = self._worktree_path(
                request.project_id, request.execution_id
            )
            file_path = os.path.join(worktree_path, request.path)
            with open(file_path) as f:
                content = f.read()
            return ReadFileResponse(
                type="read_file_response",
                request_id=request.request_id,
                success=True,
                content=content,
            )
        except FileNotFoundError:
            return ReadFileResponse(
                type="read_file_response",
                request_id=request.request_id,
                success=False,
                error="file not found",
            )
        except Exception as exc:
            return ReadFileResponse(
                type="read_file_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_setup_environment(
        self, request: SetupEnvironmentRequest
    ) -> SetupEnvironmentResponse:
        try:
            for src, dst in request.symlinks.items():
                safe_symlink(src, dst)
            return SetupEnvironmentResponse(
                type="setup_environment_response",
                request_id=request.request_id,
                success=True,
            )
        except Exception as exc:
            return SetupEnvironmentResponse(
                type="setup_environment_response",
                request_id=request.request_id,
                success=False,
                error=str(exc),
            )

    def _handle_get_status(self, request: GetStatusRequest) -> GetStatusResponse:
        return GetStatusResponse(
            type="get_status_response",
            request_id=request.request_id,
            success=True,
            current_execution_id=self._current_execution_id,
        )
