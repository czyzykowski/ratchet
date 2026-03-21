"""Worktree lifecycle: create/remove baseline and QA worktrees, symlink management."""

from __future__ import annotations

import logging
import os
import subprocess as _subprocess

logger = logging.getLogger(__name__)


class QAWorktreeError(Exception):
    """Raised when a QA worktree cannot be created."""


def find_existing_worktree(project_path: str, branch: str) -> str | None:
    """Return the path of an existing worktree checked out on branch, or None."""
    result = _subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("branch ") and current_path:
            reported = line[len("branch "):]
            if reported == f"refs/heads/{branch}" or reported == branch:
                return current_path
    return None


def safe_symlink(src: str, dst: str) -> None:
    """Create a relative symlink dst -> src, skipping if dst exists or src == dst."""
    abs_src = os.path.abspath(src)
    abs_dst = os.path.abspath(dst)
    if abs_src == abs_dst:
        logger.warning("Skipping symlink: src == dst (%s)", abs_src)
        return
    if os.path.lexists(dst):
        return
    if not os.path.exists(src):
        return
    relative_target = os.path.relpath(abs_src, os.path.dirname(abs_dst))
    os.symlink(relative_target, dst)


def create_baseline_worktree(project_path: str) -> str:
    """Create a temporary worktree on HEAD for baseline QA."""
    import uuid as _uuid

    baseline_id = str(_uuid.uuid4())[:8]
    wt_path = os.path.join(project_path, ".worktrees", f"baseline-{baseline_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", "--detach", wt_path],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise QAWorktreeError(
            f"git worktree add (baseline) failed: {result.stderr.strip()}"
        )
    safe_symlink(os.path.join(project_path, ".venv"), os.path.join(wt_path, ".venv"))
    safe_symlink(
        os.path.join(project_path, "node_modules"), os.path.join(wt_path, "node_modules")
    )
    safe_symlink(
        os.path.join(project_path, "web", "spa", "node_modules"),
        os.path.join(wt_path, "web", "spa", "node_modules"),
    )
    safe_symlink(os.path.join(project_path, ".env"), os.path.join(wt_path, ".env"))
    safe_symlink(os.path.join(project_path, ".deno"), os.path.join(wt_path, ".deno"))
    return wt_path


def create_qa_worktree(project_path: str, execution_branch: str) -> tuple[str, bool]:
    """Create a temporary worktree on the execution branch for QA testing."""
    import uuid as _uuid

    qa_id = str(_uuid.uuid4())[:8]
    qa_path = os.path.join(project_path, ".worktrees", f"qa-{qa_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", qa_path, execution_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        existing = find_existing_worktree(project_path, execution_branch)
        if existing:
            logger.info(
                "Reusing existing worktree at %s for branch %s",
                existing,
                execution_branch,
            )
            return existing, False
        raise QAWorktreeError(
            f"git worktree add failed for branch {execution_branch!r}:"
            f" {result.stderr.strip()}"
        )
    safe_symlink(os.path.join(project_path, ".venv"), os.path.join(qa_path, ".venv"))
    safe_symlink(
        os.path.join(project_path, "node_modules"), os.path.join(qa_path, "node_modules")
    )
    safe_symlink(os.path.join(project_path, ".env"), os.path.join(qa_path, ".env"))
    safe_symlink(os.path.join(project_path, ".deno"), os.path.join(qa_path, ".deno"))
    return qa_path, True


def remove_qa_worktree(project_path: str, qa_path: str) -> None:
    """Remove a temporary QA worktree."""
    try:
        _subprocess.run(
            ["git", "worktree", "remove", "--force", qa_path],
            cwd=project_path,
            check=False,
            capture_output=True,
        )
    except Exception:
        logger.warning("Failed to remove QA worktree at %s", qa_path)
