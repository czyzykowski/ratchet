# Reliable Merge Pipeline for Remote Workers

**Goal:** Fix patch transfer reliability and restructure the merge pipeline so merging and QA happen on the remote worker, with only the final commit and push on the orchestrator.

**Architecture:** The impl pipeline's `_apply_patch_to_local` is fixed to check subprocess return codes and raise on failure. The impl pipeline guards against empty diffs on completed implementations. The merge pipeline is rewritten: instead of calling `squash_merge()` locally, it derives a patch from the local execution branch, sends it to the worker via a new `patch` field on `CreateWorktreeRequest`, the worker applies it and runs QA, and on success the orchestrator applies the same patch to local develop and pushes.

**Tech Stack:** Python 3.12, Pydantic models, subprocess/git, asyncio, pytest

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `orchestrator/sequencer.py` | Modify | Fix `_apply_patch_to_local` error handling; add empty-diff guard in `run_impl_pipeline`; rewrite `run_merge_pipeline` |
| `core/remote_protocol.py` | Modify | Add optional `patch` field to `CreateWorktreeRequest` |
| `worker/executor.py` | Modify | Apply patch after creating worktree when `patch` field is present |
| `core/merge.py` | Modify | Add `apply_patch_to_develop()` helper for local merge step |
| `orchestrator/tests/test_sequencer_impl.py` | Modify | Add test for empty-diff guard |
| `orchestrator/tests/test_sequencer_merge.py` | Modify | Rewrite tests for new merge pipeline flow |
| `worker/tests/test_executor.py` | Modify | Add test for CreateWorktree with patch field |

## Tasks

### Task 1: Fix `_apply_patch_to_local` error handling

**Files:**
- Modify: `orchestrator/sequencer.py:102-179`
- Test: `orchestrator/tests/test_sequencer_impl.py`

- [x] Step 1: Add test for `_apply_patch_to_local` raising on subprocess failure

In `orchestrator/tests/test_sequencer_impl.py`, add after the existing tests:

```python
@pytest.mark.asyncio
async def test_apply_patch_to_local_raises_on_git_failure() -> None:
    """_apply_patch_to_local raises RuntimeError when git commands fail."""
    from unittest.mock import MagicMock
    import subprocess

    with patch("orchestrator.sequencer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stderr=b"fatal: bad revision", stdout=b""
        )
        with pytest.raises(RuntimeError, match="git branch.*failed"):
            PipelineSequencer._apply_patch_to_local(
                "/fake/path", "execution/test-branch", "diff content", "abc123"
            )
```

- [x] Step 2: Run test — verify it fails (current code doesn't raise)

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py::test_apply_patch_to_local_raises_on_git_failure -v
```

- [x] Step 3: Rewrite `_apply_patch_to_local` in `orchestrator/sequencer.py:102-179`

Replace the method with:

```python
@staticmethod
def _apply_patch_to_local(
    local_path: str, branch_name: str, patch_text: str,
    base_commit: str = "HEAD",
) -> None:
    """Apply a worker's patch to the orchestrator's local repo.

    Creates the execution branch if it doesn't exist, applies the patch
    via git apply, and commits. Raises RuntimeError on any git failure.
    """
    import os
    import tempfile

    # Create branch from the same base the worker used
    result = subprocess.run(
        ["git", "branch", branch_name, base_commit],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        raise RuntimeError(
            f"git branch {branch_name} failed: {result.stderr.strip()}"
        )

    # Create worktree for the branch
    wt_path = os.path.join(local_path, ".worktrees", f"patch-{branch_name.split('/')[-1]}")
    result = subprocess.run(
        ["git", "worktree", "add", wt_path, branch_name],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed: {result.stderr.strip()}"
        )

    try:
        # Write patch to temp file and apply
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False
        ) as f:
            f.write(patch_text)
            patch_file = f.name

        try:
            result = subprocess.run(
                ["git", "apply", "--allow-empty", patch_file],
                cwd=wt_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.warning(
                    "git apply failed: %s — trying with --3way",
                    result.stderr.strip(),
                )
                result = subprocess.run(
                    ["git", "apply", "--3way", patch_file],
                    cwd=wt_path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"git apply failed (both direct and --3way): {result.stderr.strip()}"
                    )
        finally:
            os.unlink(patch_file)

        # Commit the applied changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=wt_path,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"feat: worker execution ({branch_name})"],
            cwd=wt_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git commit failed: {result.stderr.strip()}"
            )
    finally:
        # Remove worktree
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt_path],
            cwd=local_path,
            capture_output=True,
        )
```

- [x] Step 4: Run test — verify it passes

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py::test_apply_patch_to_local_raises_on_git_failure -v
```

- [x] Step 5: Run all existing impl tests to confirm no regressions

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py -v
```

- [x] Step 6: Commit

```bash
git add orchestrator/sequencer.py orchestrator/tests/test_sequencer_impl.py
git commit -m "fix: _apply_patch_to_local checks return codes and raises on failure"
```

---

### Task 2: Guard against empty diff on completed implementation

**Files:**
- Modify: `orchestrator/sequencer.py:458-480` (inside `run_impl_pipeline`)
- Test: `orchestrator/tests/test_sequencer_impl.py`

**Depends on:** Task 1

- [x] Step 1: Add test for empty diff blocking the task

In `orchestrator/tests/test_sequencer_impl.py`, add:

```python
@pytest.mark.asyncio
async def test_impl_pipeline_empty_diff_blocks_task() -> None:
    """When impl completes but GetDiff returns empty patch, task is blocked."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    # Empty patch in GetDiff response
    responses = _make_channel_responses(claude_stdout="COMPLETED: done")
    # Replace the GetDiff lambda to return empty patch
    responses[4] = lambda req: GetDiffResponse(
        type="get_diff_response",
        request_id=req.request_id,
        success=True,
        patch="",
    )

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
```

Add the missing import at the top of the test file if not present:
```python
from core.remote_protocol import GetDiffResponse
```

- [x] Step 2: Run test — verify it fails (current code transitions to READY_FOR_QA)

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py::test_impl_pipeline_empty_diff_blocks_task -v
```

- [x] Step 3: Add empty-diff guard in `run_impl_pipeline`

In `orchestrator/sequencer.py`, after line 460 (`patch_text = diff_resp.patch or ""`), before the `if patch_text:` block, add:

```python
            # Guard: completed implementation must produce changes
            if is_completed and not patch_text:
                failure = "Implementation completed but produced no changes (empty diff from worker)"
                logger.error("task=%s: %s", task_id, failure)
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_fail(execution_id, failure)
                await self._state_machine.transition(
                    task_id, ev.BLOCKED,
                    extra_payload={"failure_reason": failure},
                )
                return PipelineResult(
                    success=False, task_id=task_id, execution_id=execution_id,
                    failure_reason=failure,
                )
```

- [x] Step 4: Run test — verify it passes

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py::test_impl_pipeline_empty_diff_blocks_task -v
```

- [x] Step 5: Run all impl tests

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_impl.py -v
```

- [x] Step 6: Commit

```bash
git add orchestrator/sequencer.py orchestrator/tests/test_sequencer_impl.py
git commit -m "fix: block task when implementation completes with empty diff"
```

---

### Task 3: Add `patch` field to `CreateWorktreeRequest` and worker executor

**Files:**
- Modify: `core/remote_protocol.py:170-177`
- Modify: `worker/executor.py:213-279`
- Test: `worker/tests/test_executor.py`

**Depends on:** None (independent)

- [x] Step 1: Add test for CreateWorktree with patch in `worker/tests/test_executor.py`

Find the existing `test_create_worktree` tests and add a new one:

```python
def test_create_worktree_applies_patch(tmp_path):
    """CreateWorktree with patch field applies the patch after creating worktree."""
    # Set up a git repo with a file
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "develop"], cwd=repo, capture_output=True)
    (repo / "hello.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

    # Create a patch that modifies hello.txt
    patch_text = (
        "diff --git a/hello.txt b/hello.txt\n"
        "index 0ee3856..1234567 100644\n"
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1 +1 @@\n"
        "-original\n"
        "+modified\n"
    )

    executor = CommandExecutor()
    executor._projects["proj-1"] = str(repo)

    request = CreateWorktreeRequest(
        type="create_worktree",
        request_id="req-1",
        project_id="proj-1",
        execution_id="exec-1",
        base_commit="develop",
        patch=patch_text,
    )
    response = executor._handle_create_worktree(request)

    assert response.success, response.error
    worktree_path = response.worktree_path
    assert (Path(worktree_path) / "hello.txt").read_text() == "modified\n"
```

Add necessary imports at top of test file: `from pathlib import Path`, `from core.remote_protocol import CreateWorktreeRequest`.

- [x] Step 2: Run test — verify it fails (field doesn't exist yet)

```bash
.venv/bin/python -m pytest worker/tests/test_executor.py::test_create_worktree_applies_patch -v
```

- [x] Step 3: Add `patch` field to `CreateWorktreeRequest` in `core/remote_protocol.py:170-177`

```python
class CreateWorktreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_worktree"]
    request_id: str
    project_id: str
    execution_id: str
    base_commit: str
    patch: str | None = None
```

- [x] Step 4: Update worker executor `_handle_create_worktree` in `worker/executor.py:213-279`

After the worktree is successfully created (after line 266 `self._current_execution_id = request.execution_id`), before the success return, add patch application:

```python
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
```

- [x] Step 5: Run test — verify it passes

```bash
.venv/bin/python -m pytest worker/tests/test_executor.py::test_create_worktree_applies_patch -v
```

- [x] Step 6: Run all executor tests

```bash
.venv/bin/python -m pytest worker/tests/test_executor.py -v
```

- [x] Step 7: Commit

```bash
git add core/remote_protocol.py worker/executor.py worker/tests/test_executor.py
git commit -m "feat: add optional patch field to CreateWorktreeRequest"
```

---

### Task 4: Add `apply_patch_to_develop` helper in `core/merge.py`

**Files:**
- Modify: `core/merge.py`
- Test: (tested via merge pipeline integration test in Task 5)

**Depends on:** None (independent)

- [x] Step 1: Add `apply_patch_to_develop` function to `core/merge.py`

After the `MergeResult` dataclass (line 19), add:

```python
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
            ["git", "add", "-A"], cwd=merge_worktree, capture_output=True,
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
```

- [ ] Step 2: Commit

```bash
git add core/merge.py
git commit -m "feat: add apply_patch_to_develop helper for remote merge pipeline"
```

---

### Task 5: Rewrite `run_merge_pipeline` to use remote worker

**Files:**
- Modify: `orchestrator/sequencer.py:969-1212`
- Modify: `orchestrator/tests/test_sequencer_merge.py`

**Depends on:** Tasks 3 and 4

- [ ] Step 1: Write new merge pipeline tests

Replace the content of `orchestrator/tests/test_sequencer_merge.py` with tests for the new flow. The key changes:
- No more `squash_merge` mock — the merge happens on the worker
- `CreateWorktreeRequest` now receives a `patch` field
- The sequencer calls `apply_patch_to_develop` locally after QA passes
- Tests need to mock `_get_local_head` and `subprocess.run` (for `git diff`)

```python
"""Tests for PipelineSequencer.run_merge_pipeline() — remote worker merge."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.merge import MergeResult
from core.project_manager import ProjectManager
from core.remote_protocol import (
    CreateWorktreeResponse,
    GetProjectStatusResponse,
    ReadFileResponse,
    RemoveWorktreeResponse,
    RunCommandResponse,
    SetupEnvironmentResponse,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.channel import PipelineAbort
from orchestrator.sequencer import PipelineSequencer


class MockChannel:
    def __init__(self, handler, worker_id: str = "test-worker") -> None:
        self._handler = handler
        self._worker_id = worker_id
        self.sent_requests: list = []

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def send_command(self, request):
        self.sent_requests.append(request)
        return self._handler(request)


async def _seed_merge_task(store: InMemoryStore):
    """Register project, create task + spec, transition to ready_for_deployment."""
    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "Merge task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    execution_id = uuid4()
    branch_name = f"execution/{execution_id}"
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"remote/{execution_id}",
        "branch_name": branch_name,
        "status": "running",
    }
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )

    await sm.transition(task.id, ev.READY_FOR_QA)
    await sm.transition(task.id, ev.READY_FOR_DEPLOYMENT)

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec, branch_name


def _make_handler(*, pass_qa: bool = True):
    def handler(req):
        t = req.type
        if t == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="dev-sha",
            )
        if t == "create_worktree":
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=req.request_id,
                success=True,
                worktree_path="/remote/merge-wt",
            )
        if t == "setup_environment":
            return SetupEnvironmentResponse(
                type="setup_environment_response",
                request_id=req.request_id,
                success=True,
            )
        if t == "read_file":
            return ReadFileResponse(
                type="read_file_response",
                request_id=req.request_id,
                success=True,
                content=None,
            )
        if t == "run_command":
            rc = 0 if pass_qa else 1
            return RunCommandResponse(
                type="run_command_response",
                request_id=req.request_id,
                success=True,
                returncode=rc,
                stdout="ok" if pass_qa else "FAIL",
                stderr="",
            )
        if t == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected request type: {t!r}")

    return handler


_RATCHET_YAML = "qa:\n  steps:\n    test: pytest\n"


@pytest.mark.asyncio
async def test_merge_pipeline_happy_path_transitions_to_deployed() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=True))
    sequencer = PipelineSequencer(store)
    project.ratchet_yaml = _RATCHET_YAML

    fake_apply = MergeResult(success=True, new_sha="merged-sha")
    fake_diff = MagicMock(returncode=0, stdout="diff --git a/f.txt b/f.txt\n+new\n")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer.apply_patch_to_develop", return_value=fake_apply),
        patch("orchestrator.sequencer._push_branch"),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.DEPLOYED

    # Verify CreateWorktree received the patch
    create_wt = [r for r in channel.sent_requests if r.type == "create_worktree"]
    assert len(create_wt) == 1
    assert create_wt[0].patch is not None


@pytest.mark.asyncio
async def test_merge_pipeline_qa_failure_blocks_without_local_changes() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=False))
    sequencer = PipelineSequencer(store)
    project.ratchet_yaml = _RATCHET_YAML

    fake_diff = MagicMock(returncode=0, stdout="diff content")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer.apply_patch_to_develop") as apply_mock,
        patch("orchestrator.sequencer._push_branch") as push_mock,
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
    # Local develop should NOT be modified
    apply_mock.assert_not_called()
    push_mock.assert_not_called()


@pytest.mark.asyncio
async def test_merge_pipeline_missing_execution_branch_blocks() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=True))
    sequencer = PipelineSequencer(store)

    # git diff returns error (branch doesn't exist)
    fake_diff = MagicMock(returncode=128, stdout="", stderr="fatal: bad revision")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
    assert "execution branch" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_merge_pipeline_worker_abort_blocks() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    def handler(req):
        if req.type == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="dev-sha",
            )
        if req.type == "create_worktree":
            raise PipelineAbort("create_worktree", "network error", req.request_id)
        if req.type == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected: {req.type!r}")

    channel = MockChannel(handler)
    sequencer = PipelineSequencer(store)

    fake_diff = MagicMock(returncode=0, stdout="diff content")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer._push_branch"),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
```

- [ ] Step 2: Run new tests — verify they fail (old merge pipeline)

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_merge.py -v
```

- [ ] Step 3: Rewrite `run_merge_pipeline` in `orchestrator/sequencer.py`

Replace the method body (lines 969-1212). Add `from core.merge import apply_patch_to_develop` at the top of the file alongside the existing `from core.merge import squash_merge` import. The new method:

```python
async def run_merge_pipeline(
    self,
    channel: WorkerChannel,
    task: Task,
    project: Project,
) -> PipelineResult:
    """Drive merge pipeline: derive patch → send to worker for QA → apply locally.

    Steps:
    1. Find execution branch from task events
    2. Derive patch via git diff develop..execution/xxx
    3. Send to worker: CreateWorktree (with patch) + SetupEnvironment + QA
    4. If QA passes: apply patch to local develop, run hooks, push
    5. Transition to DEPLOYED or BLOCKED
    """
    task_id = task.id
    project_id = project.id
    execution_id: UUID | None = None
    target_branch = "develop"
    local_path = project.local_path

    # Find execution branch from task events
    execution_events = await self._store.get_events(task_id, "task_executions")
    execution_branch: str | None = None
    spec_id_val: UUID | None = None
    for event in reversed(execution_events):
        if event.event_type == ev.EXECUTION_STARTED:
            bn = event.payload.get("branch_name")
            if bn and bn.startswith("execution/"):
                si = event.payload.get("spec_id")
                execution_branch = bn
                spec_id_val = UUID(si) if si else None
                break

    if not execution_branch:
        failure = "Merge cannot run: no execution branch found for task"
        logger.error("task=%s: %s", task_id, failure)
        await self._state_machine.transition(
            task_id, ev.BLOCKED, extra_payload={"failure_reason": failure}
        )
        return PipelineResult(
            success=False, task_id=task_id, failure_reason=failure
        )

    # Derive patch from local execution branch
    diff_result = subprocess.run(
        ["git", "diff", f"{target_branch}...{execution_branch}"],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if diff_result.returncode != 0 or not diff_result.stdout.strip():
        failure = (
            f"Merge cannot run: execution branch {execution_branch} "
            f"not found locally or has no changes"
        )
        logger.error("task=%s: %s", task_id, failure)
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_AUTO_MERGE_FAILED,
            payload={"failure_reason": failure},
        )
        await self._state_machine.transition(
            task_id, ev.BLOCKED, extra_payload={"failure_reason": failure}
        )
        return PipelineResult(
            success=False, task_id=task_id, failure_reason=failure
        )

    patch_text = diff_result.stdout

    try:
        # Step 1: ensure worker has the project
        await self._ensure_project_on_worker(channel, project)

        # Step 2: create execution record
        qa_branch_name = f"merge-qa/{uuid4()}"
        execution_id = await self._record_execution_start(
            task_id, spec_id_val or uuid4(), qa_branch_name,
        )
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_ASSIGNED_TO_WORKER,
            payload={"worker_id": channel.worker_id, "execution_id": str(execution_id)},
        )

        # Step 3: CreateWorktree on develop HEAD with patch applied
        head_commit = _get_local_head(local_path)
        create_wt_req = CreateWorktreeRequest(
            type="create_worktree",
            request_id=str(uuid4()),
            project_id=str(project_id),
            execution_id=str(execution_id),
            base_commit=head_commit,
            patch=patch_text,
        )
        create_wt_resp = await channel.send_command(create_wt_req)
        assert isinstance(create_wt_resp, CreateWorktreeResponse)
        worktree_path = create_wt_resp.worktree_path or f"/remote/{execution_id}"

        # Step 4: SetupEnvironment
        setup_env_req = SetupEnvironmentRequest(
            type="setup_environment",
            request_id=str(uuid4()),
            project_id=str(project_id),
            execution_id=str(execution_id),
            symlinks=_STANDARD_SYMLINKS,
        )
        await channel.send_command(setup_env_req)

        # Step 5: load QA config and run steps
        qa_config = None
        if project.ratchet_yaml:
            qa_config = load_qa_config_from_string(project.ratchet_yaml)

        if qa_config is not None and qa_config.auto_fix:
            for fix_cmd in qa_config.auto_fix:
                autofix_req = RunCommandRequest(
                    type="run_command",
                    request_id=str(uuid4()),
                    execution_id=str(execution_id),
                    cmd=["bash", "-c", fix_cmd],
                    cwd=worktree_path,
                )
                try:
                    await channel.send_command(autofix_req)
                except PipelineAbort:
                    pass

        failed_steps: list[tuple[str, str]] = []
        if qa_config is not None:
            for step in qa_config.steps:
                cmd_req = RunCommandRequest(
                    type="run_command",
                    request_id=str(uuid4()),
                    execution_id=str(execution_id),
                    cmd=["bash", "-c", step.command],
                    cwd=worktree_path,
                )
                try:
                    cmd_resp = await channel.send_command(cmd_req)
                    assert isinstance(cmd_resp, RunCommandResponse)
                    if cmd_resp.returncode != 0:
                        output = (cmd_resp.stdout or "") + (cmd_resp.stderr or "")
                        failed_steps.append((step.name, output))
                        break
                except PipelineAbort as exc:
                    failed_steps.append((step.name, exc.error))
                    break

        if not failed_steps:
            # QA passed — clean up worker, apply locally, push
            await self._try_remove_worktree(channel, project_id, execution_id)
            await self._record_execution_complete(execution_id)

            # Apply patch to local develop
            merge_result = await asyncio.to_thread(
                apply_patch_to_develop,
                local_path,
                patch_text,
                task.title,
                task_id,
                target_branch,
            )
            if not merge_result.success:
                failure = merge_result.failure_reason or "local patch apply failed"
                logger.error("task=%s local apply failed: %s", task_id, failure)
                await self._store.append_event(
                    aggregate_id=task_id,
                    aggregate_type="task",
                    event_type=ev.TASK_AUTO_MERGE_FAILED,
                    payload={"failure_reason": failure},
                )
                await self._state_machine.transition(
                    task_id, ev.BLOCKED, extra_payload={"failure_reason": failure}
                )
                return PipelineResult(
                    success=False, task_id=task_id, failure_reason=failure
                )

            # Run merge/deploy hooks
            from core.qa_runner import load_merge_config, run_merge_steps

            merge_config = load_merge_config(local_path)
            if merge_config is not None and merge_config.steps:
                hook_results = await asyncio.to_thread(
                    run_merge_steps, merge_config, local_path
                )
                await self._store.append_event(
                    aggregate_id=task_id,
                    aggregate_type="task",
                    event_type=ev.TASK_DEPLOY_HOOKS_RUN,
                    payload={
                        "steps": [
                            {
                                "name": r.step_name,
                                "command": r.command,
                                "returncode": r.returncode,
                                "output": r.output,
                            }
                            for r in hook_results
                        ]
                    },
                )

            # Delete the execution branch (no longer needed)
            await asyncio.to_thread(
                subprocess.run,
                ["git", "branch", "-D", execution_branch],
                **{"cwd": local_path, "capture_output": True},
            )

            # Push
            _push_branch(local_path, target_branch)
            await self._state_machine.transition(task_id, ev.DEPLOYED)
            logger.info(
                "Merge pipeline succeeded for task=%s (sha=%s)",
                task_id, merge_result.new_sha,
            )
            return PipelineResult(
                success=True, task_id=task_id, execution_id=execution_id
            )
        else:
            # QA failed — block task, local develop untouched
            combined_output = "\n\n".join(
                f"Step '{name}':\n{output}" for name, output in failed_steps
            )
            logger.warning(
                "Merge QA failed for task=%s: %s", task_id, combined_output[:200]
            )
            await self._try_remove_worktree(channel, project_id, execution_id)
            await self._record_execution_fail(execution_id, combined_output)
            await self._state_machine.transition(
                task_id, ev.BLOCKED,
                extra_payload={"failure_reason": combined_output},
            )
            return PipelineResult(
                success=False, task_id=task_id, execution_id=execution_id,
                failure_reason=combined_output,
            )

    except PipelineAbort as exc:
        logger.error(
            "Merge pipeline aborted for task=%s at step=%s: %s",
            task_id, exc.step_name, exc.error,
        )
        failure = f"Pipeline aborted at {exc.step_name!r}: {exc.error}"
        return await self._abort_pipeline(
            channel, task_id, project_id, execution_id, failure
        )
```

Update the import at the top of `orchestrator/sequencer.py`:
```python
from core.merge import apply_patch_to_develop, squash_merge  # squash_merge kept for backward compat
```

- [ ] Step 4: Run merge tests — verify they pass

```bash
.venv/bin/python -m pytest orchestrator/tests/test_sequencer_merge.py -v
```

- [ ] Step 5: Run full test suite

```bash
.venv/bin/python -m pytest core/tests/ orchestrator/tests/ web/tests/ worker/tests/ --ignore=orchestrator/tests/test_channel.py -v
```

- [ ] Step 6: Type check and lint

```bash
.venv/bin/python -m mypy core/ orchestrator/ web/
.venv/bin/python -m ruff check core/ orchestrator/ web/
```

- [ ] Step 7: Commit

```bash
git add orchestrator/sequencer.py orchestrator/tests/test_sequencer_merge.py
git commit -m "feat: rewrite merge pipeline to use remote worker for QA verification"
```

## Verification

After all tasks are complete:

```bash
# Full test suite
.venv/bin/python -m pytest core/tests/ orchestrator/tests/ web/tests/ worker/tests/ --ignore=orchestrator/tests/test_channel.py -v

# Type check
.venv/bin/python -m mypy core/ orchestrator/ web/

# Lint
.venv/bin/python -m ruff check core/ orchestrator/ web/

# Manual: verify the pheme merge task can now proceed
.venv/bin/python scripts/task-status.py --task-id f0bb610f-094e-4a47-86d2-f89c107f649a
```
