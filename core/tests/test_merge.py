"""Unit tests for core.merge.squash_merge (subprocess mocked)."""

from __future__ import annotations

import subprocess
import uuid
from unittest.mock import MagicMock, patch

from core.invoker import InvocationResult
from core.merge import squash_merge
from core.store import InMemoryStore

FAKE_LOCAL_PATH = "/fake/repo"
FAKE_BRANCH = "task/exec-abc123"
FAKE_TARGET = "develop"
FAKE_TITLE = "My task"
FAKE_TASK_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

_FAKE_SHA = "deadbeef1234567890"


def _completed_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr.encode() if isinstance(stderr, str) else stderr
    return proc


def _make_invoker(status: str = "completed", failure_reason: str | None = None) -> MagicMock:
    invoker = MagicMock()
    invoker.invoke.return_value = InvocationResult(
        execution_id=uuid.uuid4(),
        status=status,
        failure_reason=failure_reason,
        trace_id=None,
    )
    return invoker


def _squash_merge_kwargs(
    invoker=None,
    spec_content: str = "",
    intent_content: str = "intent",
    store=None,
):
    return dict(
        local_path=FAKE_LOCAL_PATH,
        execution_branch=FAKE_BRANCH,
        target_branch=FAKE_TARGET,
        title=FAKE_TITLE,
        task_id=FAKE_TASK_ID,
        store=store or InMemoryStore(),
        invoker=invoker,
        spec_content=spec_content,
        intent_content=intent_content,
    )


class TestSquashMergeHappyPath:
    def test_should_return_success_with_new_sha_when_merge_succeeds(self):
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                return _completed_proc()
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _completed_proc(returncode=1)  # has staged changes
            if cmd[:3] == ["git", "commit", "-m"]:
                return _completed_proc()
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            if cmd[:3] == ["git", "worktree", "remove"]:
                return _completed_proc()
            if cmd[:2] == ["git", "update-ref"]:
                return _completed_proc()
            if cmd[:2] == ["git", "checkout"]:
                return _completed_proc()
            if cmd[:2] == ["git", "reset"]:
                return _completed_proc()
            if cmd[:3] == ["git", "branch", "-D"]:
                return _completed_proc()
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs())

        assert result.success is True
        assert result.new_sha == _FAKE_SHA
        assert result.failure_reason is None

    def test_should_skip_commit_when_no_staged_changes(self):
        committed = []

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                return _completed_proc()
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _completed_proc(returncode=0)  # no staged changes
            if cmd[:3] == ["git", "commit", "-m"]:
                committed.append(True)
                return _completed_proc()
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs())

        assert result.success is True
        assert not committed  # commit was not called


class TestSquashMergeWorktreeFailure:
    def test_should_return_failure_when_worktree_add_fails(self):
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"no space left")
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs())

        assert result.success is False
        assert "no space left" in (result.failure_reason or "")


class TestSquashMergeConflict:
    def test_should_return_failure_when_invoker_is_none_on_conflict(self):
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"CONFLICT")
            if cmd[:3] == ["git", "diff", "--name-only"]:
                p = _completed_proc()
                p.stdout = "file.py\n"
                return p
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            if cmd[:3] == ["git", "worktree", "remove"]:
                return _completed_proc()
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs(invoker=None))

        assert result.success is False
        assert "no invoker" in (result.failure_reason or "")

    def test_should_return_success_when_claude_resolves_conflict(self):
        invoker = _make_invoker(status="completed")

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"CONFLICT")
            if cmd[:3] == ["git", "diff", "--name-only"]:
                p = _completed_proc()
                p.stdout = "file.py\n"
                return p
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _completed_proc(returncode=1)  # staged after resolution
            if cmd[:3] == ["git", "commit", "-m"]:
                return _completed_proc()
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            if cmd[:3] == ["git", "worktree", "remove"]:
                return _completed_proc()
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs(invoker=invoker))

        assert result.success is True
        assert result.new_sha == _FAKE_SHA
        invoker.invoke.assert_called_once()

    def test_should_return_failure_when_claude_fails_to_resolve_conflict(self):
        invoker = _make_invoker(status="failed", failure_reason="could not resolve")

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"CONFLICT")
            if cmd[:3] == ["git", "diff", "--name-only"]:
                p = _completed_proc()
                p.stdout = "file.py\n"
                return p
            if cmd[:3] == ["git", "merge", "--abort"]:
                return _completed_proc()
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            if cmd[:3] == ["git", "worktree", "remove"]:
                return _completed_proc()
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs(invoker=invoker))

        assert result.success is False
        assert "could not resolve" in (result.failure_reason or "")

    def test_should_abort_merge_before_returning_failure_on_resolution_failure(self):
        invoker = _make_invoker(status="failed", failure_reason="blocked")
        aborted = []

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "worktree", "add"]:
                return _completed_proc()
            if cmd[:3] == ["git", "merge", "--squash"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"CONFLICT")
            if cmd[:3] == ["git", "diff", "--name-only"]:
                p = _completed_proc()
                p.stdout = "foo.py\n"
                return p
            if cmd[:3] == ["git", "merge", "--abort"]:
                aborted.append(True)
                return _completed_proc()
            if cmd[:2] == ["git", "rev-parse"]:
                return _completed_proc(stdout=_FAKE_SHA)
            if cmd[:3] == ["git", "worktree", "remove"]:
                return _completed_proc()
            return _completed_proc()

        with patch("subprocess.run", side_effect=fake_run):
            result = squash_merge(**_squash_merge_kwargs(invoker=invoker))

        assert result.success is False
        assert aborted  # git merge --abort was called
