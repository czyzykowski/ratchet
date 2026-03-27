"""Shared squash-merge logic for local deployment mode."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from uuid import UUID
from uuid import uuid4 as _uuid4

from core.invoker import ClaudeCodeInvoker
from core.store import Store


@dataclass
class MergeResult:
    success: bool
    failure_reason: str | None = None
    new_sha: str | None = None


def apply_patch_to_develop(
    local_path: str,
    patch_text: str,
    title: str,
    task_id: UUID,
    target_branch: str = "develop",
) -> MergeResult:
    """Apply a pre-verified patch to the target branch.

    Used after remote worker QA verifies the merge. Creates a temporary
    worktree, applies the patch, commits, and advances the target branch.

    Returns MergeResult with success=True and new_sha on success.
    """
    merge_worktree = os.path.join(local_path, ".worktrees", f"deploy-{_uuid4()}")
    new_sha: str | None = None

    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", merge_worktree, target_branch],
            cwd=local_path,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        return MergeResult(
            success=False,
            failure_reason=f"git worktree add failed: {exc.stderr.decode()}",
        )

    try:
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False
        ) as f:
            f.write(patch_text)
            patch_file = f.name

        try:
            result = subprocess.run(
                ["git", "apply", "--allow-empty", patch_file],
                cwd=merge_worktree,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return MergeResult(
                    success=False,
                    failure_reason=f"git apply failed: {result.stderr.strip()}",
                )
        finally:
            os.unlink(patch_file)

        # Stage and commit
        subprocess.run(
            ["git", "add", "-A"],
            cwd=merge_worktree,
            capture_output=True,
        )
        commit_msg = f"feat: {title} (task/{task_id})"
        try:
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", commit_msg],
                cwd=merge_worktree,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            return MergeResult(
                success=False,
                failure_reason=f"git commit failed: {exc.stderr.decode()}",
            )
    finally:
        new_sha_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=merge_worktree,
            capture_output=True,
            text=True,
        )
        new_sha = new_sha_proc.stdout.strip() or None
        subprocess.run(
            ["git", "worktree", "remove", "--force", merge_worktree],
            cwd=local_path,
            capture_output=True,
        )

    # Advance target branch
    if new_sha:
        subprocess.run(
            ["git", "update-ref", f"refs/heads/{target_branch}", new_sha],
            cwd=local_path,
            capture_output=True,
        )
    subprocess.run(
        ["git", "checkout", target_branch],
        cwd=local_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "reset", "--hard", target_branch],
        cwd=local_path,
        capture_output=True,
    )

    return MergeResult(success=True, new_sha=new_sha)


def squash_merge(
    local_path: str,
    execution_branch: str,
    target_branch: str,
    title: str,
    task_id: UUID,
    store: Store,
    invoker: ClaudeCodeInvoker | None,
    spec_content: str,
    intent_content: str,
) -> MergeResult:
    """Squash-merge execution_branch into target_branch within a temporary worktree.

    Steps:
    1. Create worktree at .worktrees/deploy-<uuid> detached at target_branch
    2. git merge --squash <execution_branch>
    3. On conflict: attempt Claude-assisted resolution if invoker is not None
    4. Commit staged changes
    5. Update ref, sync working directory, delete execution branch

    Returns MergeResult with success=True and new_sha on success,
    or success=False and failure_reason on any failure.
    """
    merge_worktree = os.path.join(local_path, ".worktrees", f"deploy-{_uuid4()}")
    new_sha: str | None = None

    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", merge_worktree, target_branch],
            cwd=local_path,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        return MergeResult(
            success=False,
            failure_reason=f"git worktree add failed: {exc.stderr.decode()}",
        )

    try:
        try:
            subprocess.run(
                ["git", "merge", "--squash", execution_branch],
                cwd=merge_worktree,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as merge_exc:
            merge_output = merge_exc.stderr.decode()

            # Get list of conflicted files
            conflict_result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=merge_worktree,
                capture_output=True,
                text=True,
            )
            conflicted_files = [
                f for f in conflict_result.stdout.strip().splitlines() if f
            ]

            if invoker is None:
                return MergeResult(
                    success=False,
                    failure_reason=f"Merge conflict (no invoker for resolution): {merge_output}",
                )

            from core.context_assembler import ExecutionContext, build_conflict_resolution_prompt

            prompt = build_conflict_resolution_prompt(
                intent_content=intent_content,
                spec_content=spec_content,
                conflicted_files=conflicted_files,
                merge_output=merge_output,
            )

            resolution_execution_id = _uuid4()
            context = ExecutionContext(
                execution_id=resolution_execution_id,
                task_id=task_id,
                spec_id=task_id,  # use task_id as placeholder when spec_id unavailable
                worktree_path=merge_worktree,
                prompt=prompt,
            )
            result = invoker.invoke(context)

            if result.status != "completed":
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=merge_worktree,
                    capture_output=True,
                )
                failure_reason = (
                    f"Merge conflict: {merge_output}\n"
                    f"Conflict resolution failed: {result.failure_reason}"
                )
                return MergeResult(success=False, failure_reason=failure_reason)

        has_staged = (
            subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=merge_worktree,
                capture_output=True,
            ).returncode
            != 0
        )
        if has_staged:
            commit_msg = f"feat: {title} (task/{task_id})"
            try:
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=merge_worktree,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                return MergeResult(
                    success=False,
                    failure_reason=f"git commit failed: {exc.stderr.decode()}",
                )

    finally:
        new_sha_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=merge_worktree,
            capture_output=True,
            text=True,
        )
        new_sha = new_sha_proc.stdout.strip() or None
        subprocess.run(
            ["git", "worktree", "remove", "--force", merge_worktree],
            cwd=local_path,
            capture_output=True,
        )

    # Advance target branch and sync working directory
    if new_sha:
        subprocess.run(
            ["git", "update-ref", f"refs/heads/{target_branch}", new_sha],
            cwd=local_path,
            capture_output=True,
        )
    subprocess.run(
        ["git", "checkout", target_branch],
        cwd=local_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "reset", "--hard", target_branch],
        cwd=local_path,
        capture_output=True,
    )

    try:
        subprocess.run(
            ["git", "branch", "-D", execution_branch],
            cwd=local_path,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass  # non-fatal — branch may already be gone

    return MergeResult(success=True, new_sha=new_sha)
