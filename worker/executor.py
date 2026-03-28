"""Stateless command executor for remote worker protocol."""

from __future__ import annotations

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
    GetSessionProgressRequest,
    GetSessionProgressResponse,
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
SESSION_PROGRESS_TAIL = 20


class CommandExecutor:
    """Handles command protocol requests from the orchestrator.

    Stateless per execution: only tracks current_execution_id and the
    projects dict. Each handler is independently testable.
    """

    def __init__(
        self,
        worker_id: str,
        projects: dict[str, str],
        workspace: str = "",
        sandbox_name: str = "auto",
    ) -> None:
        self._worker_id = worker_id
        self._projects: dict[str, str] = dict(projects)
        self._workspace = workspace
        self._current_execution_id: str | None = None
        self._base_commit: str | None = None
        self._current_cwd: str | None = None

        from core.sandbox import NullSandbox, Sandbox, default_registry  # noqa: F401

        resolved_sandbox: Sandbox
        if sandbox_name == "none":
            resolved_sandbox = NullSandbox()
        elif sandbox_name == "auto":
            import threading as _threading

            _result: list[Sandbox] = []
            _exc: list[BaseException] = []

            def _run_auto_detect() -> None:
                import asyncio as _asyncio

                _loop = _asyncio.new_event_loop()
                try:
                    _result.append(_loop.run_until_complete(default_registry.auto_detect()))
                except Exception as e:
                    _exc.append(e)
                finally:
                    _loop.close()

            _t = _threading.Thread(target=_run_auto_detect, daemon=True)
            _t.start()
            _t.join()
            if _exc:
                raise _exc[0]
            resolved_sandbox = _result[0]
        else:
            resolved_sandbox = default_registry.get(sandbox_name)
        self._sandbox: Sandbox = resolved_sandbox
        logger.info("Sandbox backend: %s", self._sandbox.name())

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
            return await self._handle_remove_worktree(request)
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
        elif isinstance(request, GetSessionProgressRequest):
            return self._handle_get_session_progress(request)
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
            bundle_bytes = base64.b64decode(request.patch_b64)
            # The "patch" is actually a git bundle — fetch from it
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as f:
                f.write(bundle_bytes)
                bundle_path = f.name
            try:
                result = subprocess.run(
                    ["git", "fetch", bundle_path],
                    cwd=path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    # Fall back to treating as text patch
                    try:
                        patch_text = bundle_bytes.decode("utf-8")
                        git_transfer.apply_patch(path, patch_text)
                    except Exception:
                        raise RuntimeError(
                            f"git fetch from bundle failed: {result.stderr.strip()}"
                        )
                else:
                    # Reset to fetched HEAD
                    subprocess.run(
                        ["git", "reset", "--hard", "FETCH_HEAD"],
                        cwd=path,
                        capture_output=True,
                    )
            finally:
                os.unlink(bundle_path)
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
            self._base_commit = base
            self._current_cwd = worktree_path
            # Apply patch if provided (used by merge pipeline)
            if request.patch:
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".patch", delete=False
                ) as f:
                    f.write(request.patch)
                    patch_file = f.name
                try:
                    apply_result = subprocess.run(
                        ["git", "apply", "--allow-empty", patch_file],
                        cwd=worktree_path,
                        capture_output=True,
                        text=True,
                    )
                    if apply_result.returncode != 0:
                        return CreateWorktreeResponse(
                            type="create_worktree_response",
                            request_id=request.request_id,
                            success=False,
                            error=f"git apply failed: {apply_result.stderr.strip()}",
                        )
                finally:
                    os.unlink(patch_file)
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

    async def _handle_remove_worktree(
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
            try:
                await self._sandbox.cleanup()
            except Exception as exc:
                logger.warning("Sandbox cleanup failed: %s", exc)
            if self._current_execution_id == request.execution_id:
                self._current_execution_id = None
                self._current_cwd = None
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
            patch = git_transfer.create_patch(worktree_path, self._base_commit)
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

    @staticmethod
    def _read_session_jsonl(cwd: str) -> str | None:
        """Read the most recent Claude session JSONL for the given working directory."""
        import glob as _glob

        slug = os.path.abspath(cwd).replace("/", "-").replace(".", "-")
        project_dir = os.path.join(
            os.path.expanduser("~"), ".claude", "projects", slug
        )
        if not os.path.isdir(project_dir):
            return None
        jsonl_files = sorted(
            _glob.glob(os.path.join(project_dir, "*.jsonl")),
            key=os.path.getmtime,
        )
        if not jsonl_files:
            return None
        try:
            with open(jsonl_files[-1]) as f:
                return f.read()
        except OSError:
            return None

    @staticmethod
    def _read_session_jsonl_from(base_claude_dir: str, cwd: str) -> str | None:
        """Read session JSONL from a custom base directory (e.g., sandbox ephemeral path)."""
        import glob as _glob

        slug = os.path.abspath(cwd).replace("/", "-").replace(".", "-")
        project_dir = os.path.join(base_claude_dir, "projects", slug)
        if not os.path.isdir(project_dir):
            return None
        jsonl_files = sorted(
            _glob.glob(os.path.join(project_dir, "*.jsonl")),
            key=os.path.getmtime,
        )
        if not jsonl_files:
            return None
        try:
            with open(jsonl_files[-1]) as f:
                return f.read()
        except OSError:
            return None

    async def _handle_run_claude(self, request: RunClaudeRequest) -> RunClaudeResponse:
        try:
            from core.sandbox import build_sandbox_config

            worktree_cwd = request.cwd
            wt_marker = "/.worktrees/"
            if wt_marker in worktree_cwd:
                project_path = worktree_cwd[: worktree_cwd.index(wt_marker)]
            else:
                project_path = worktree_cwd

            sandbox_config = build_sandbox_config(
                worktree_path=worktree_cwd,
                project_path=project_path,
                symlinked_dirs=[".venv", "node_modules", ".env", ".deno"],
            )

            claude_request = ClaudeRequest(
                prompt=request.prompt,
                cwd=request.cwd,
                model=request.model,
                allowed_tools=",".join(request.tools),
                sandbox=self._sandbox,
                sandbox_config=sandbox_config,
            )
            self._current_execution_id = request.execution_id
            try:
                result = await claude_subprocess.async_start(claude_request)
            except Exception as exc:
                return RunClaudeResponse(
                    type="run_claude_response",
                    request_id=request.request_id,
                    success=True,
                    stdout="",
                    stderr=f"Sandbox start failed: {exc}",
                    returncode=1,
                    status="failed",
                    session_jsonl=None,
                )
            status = "completed" if result.returncode == 0 else "failed"
            ephemeral_claude = self._sandbox.get_ephemeral_path("~/.claude")
            if ephemeral_claude is not None:
                session_jsonl = self._read_session_jsonl_from(ephemeral_claude, request.cwd)
            else:
                session_jsonl = self._read_session_jsonl(request.cwd)
            return RunClaudeResponse(
                type="run_claude_response",
                request_id=request.request_id,
                success=True,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                status=status,
                session_jsonl=session_jsonl,
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
            from pathlib import Path

            cmd = request.cmd
            # Wrap with nix develop if flake.nix exists in the working directory
            if request.cwd and (Path(request.cwd) / "flake.nix").exists():
                cmd = ["nix", "develop", "--command"] + cmd
            result = subprocess.run(
                cmd,
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
            project_path = self._projects[request.project_id]
            worktree_path = self._worktree_path(request.project_id, request.execution_id)
            logger.info(
                "setup_environment: project=%s worktree=%s symlinks=%s",
                project_path, worktree_path, request.symlinks,
            )
            for name in request.symlinks:
                src = os.path.join(project_path, name)
                dst = os.path.join(worktree_path, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                safe_symlink(src, dst)
                logger.info("  symlink: %s -> %s (exists=%s)", dst, src, os.path.lexists(dst))
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

    def _handle_get_session_progress(
        self, request: GetSessionProgressRequest
    ) -> GetSessionProgressResponse:
        import glob as _glob
        import json

        if self._current_cwd is None:
            return GetSessionProgressResponse(
                type="get_session_progress_response",
                request_id=request.request_id,
                success=True,
                messages=[],
                total_messages=0,
                file_size_bytes=0,
            )

        slug = os.path.abspath(self._current_cwd).replace("/", "-").replace(".", "-")
        ephemeral_claude = self._sandbox.get_ephemeral_path("~/.claude")
        base_claude = ephemeral_claude if ephemeral_claude is not None else os.path.join(
            os.path.expanduser("~"), ".claude"
        )
        project_dir = os.path.join(base_claude, "projects", slug)
        if not os.path.isdir(project_dir):
            return GetSessionProgressResponse(
                type="get_session_progress_response",
                request_id=request.request_id,
                success=True,
                messages=[],
                total_messages=0,
                file_size_bytes=0,
            )

        jsonl_files = sorted(
            _glob.glob(os.path.join(project_dir, "*.jsonl")),
            key=os.path.getmtime,
        )
        if not jsonl_files:
            return GetSessionProgressResponse(
                type="get_session_progress_response",
                request_id=request.request_id,
                success=True,
                messages=[],
                total_messages=0,
                file_size_bytes=0,
            )

        jsonl_path = jsonl_files[-1]
        file_size = os.path.getsize(jsonl_path)
        parsed: list[dict[str, object]] = []
        try:
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # skip incomplete last line
        except OSError:
            pass

        total = len(parsed)
        tail = parsed[-SESSION_PROGRESS_TAIL:] if total > SESSION_PROGRESS_TAIL else parsed

        return GetSessionProgressResponse(
            type="get_session_progress_response",
            request_id=request.request_id,
            success=True,
            messages=tail,
            total_messages=total,
            file_size_bytes=file_size,
        )
