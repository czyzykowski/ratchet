"""Orchestrator-side pipeline sequencer.

Drives remote workers through multi-step command sequences for implementation,
QA, and merge pipelines. Sends command requests to workers over WorkerChannel,
awaits typed responses, and sequences the next step based on results.
"""

from __future__ import annotations

import base64
import logging
import subprocess
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from core import events as ev
from core import git_transfer
from core.context_assembler import build_prompt, read_intent
from core.merge import squash_merge
from core.models import Project, Spec, Task
from core.models_config import WORKER_MODEL
from core.qa_runner import load_qa_config_from_string
from core.remote_protocol import (
    CreateWorktreeRequest,
    CreateWorktreeResponse,
    GetDiffRequest,
    GetDiffResponse,
    GetProjectStatusRequest,
    GetProjectStatusResponse,
    ReadFileRequest,
    ReadFileResponse,
    RemoveWorktreeRequest,
    RunClaudeRequest,
    RunClaudeResponse,
    RunCommandRequest,
    RunCommandResponse,
    SetupEnvironmentRequest,
    SetupProjectRequest,
    UpdateProjectRequest,
)
from core.state_machine import TaskStateMachine
from core.store import Store
from orchestrator.channel import PipelineAbort, WorkerChannel

logger = logging.getLogger(__name__)

# Relative paths to symlink from project root into each worktree.
# Keys are names relative to project root; the executor resolves
# them to absolute src (project_root/name) → dst (worktree/name).
_STANDARD_SYMLINKS = [
    ".venv",
    "node_modules",
    ".env",
    ".deno",
    "web/spa/node_modules",
]


@dataclass
class PipelineResult:
    """Result of running a pipeline."""

    success: bool
    task_id: UUID
    execution_id: UUID | None = None
    failure_reason: str | None = None


def _get_local_head(local_path: str) -> str:
    """Get the current HEAD commit SHA of the local repo."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "HEAD"
    return result.stdout.strip()


def _create_patch_bundle_b64(local_path: str) -> str:
    """Create a git bundle of the entire local repo, base64-encoded."""
    bundle_bytes = git_transfer.create_bundle(local_path)
    return base64.b64encode(bundle_bytes).decode()


class PipelineSequencer:
    """Drives remote workers through pipeline stages via WorkerChannel."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._state_machine = TaskStateMachine(store)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _record_execution_start(
        self, task_id: UUID, spec_id: UUID, branch_name: str,
        execution_id: UUID | None = None,
    ) -> UUID:
        """Record EXECUTION_STARTED events without creating a local worktree.

        Returns the execution_id (generated if not provided).
        """
        if execution_id is None:
            execution_id = uuid4()
        worktree_path = f"remote/{execution_id}"
        payload = {
            "execution_id": str(execution_id),
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "worktree_path": worktree_path,
            "branch_name": branch_name,
            "status": "running",
        }
        await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_STARTED,
            payload=payload,
        )
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload=payload,
        )
        return execution_id

    async def _record_execution_complete(self, execution_id: UUID) -> None:
        await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_COMPLETED,
            payload={"execution_id": str(execution_id), "status": "completed"},
        )

    async def _record_execution_fail(
        self, execution_id: UUID, failure_reason: str
    ) -> None:
        await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_FAILED,
            payload={
                "execution_id": str(execution_id),
                "failure_reason": failure_reason,
                "status": "failed",
            },
        )

    async def _try_remove_worktree(
        self,
        channel: WorkerChannel,
        project_id: UUID,
        execution_id: UUID,
    ) -> None:
        """Best-effort RemoveWorktree — logs on failure, never raises."""
        try:
            req = RemoveWorktreeRequest(
                type="remove_worktree",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
            )
            await channel.send_command(req)
        except Exception:
            logger.warning(
                "Best-effort RemoveWorktree failed for execution=%s", execution_id
            )

    async def _abort_pipeline(
        self,
        channel: WorkerChannel,
        task_id: UUID,
        project_id: UUID,
        execution_id: UUID | None,
        failure: str,
    ) -> PipelineResult:
        """Clean up and transition task to BLOCKED after a pipeline failure."""
        if execution_id is not None:
            await self._try_remove_worktree(channel, project_id, execution_id)
            await self._record_execution_fail(execution_id, failure)

        current = await self._state_machine.get_current_status(task_id)
        if current in (ev.READY_FOR_IMPLEMENTATION, ev.IN_PROGRESS,
                       ev.READY_FOR_QA, ev.READY_FOR_DEPLOYMENT):
            if current == ev.READY_FOR_IMPLEMENTATION:
                # Transition via IN_PROGRESS first since READY_FOR_IMPLEMENTATION → BLOCKED is valid
                pass
            await self._state_machine.transition(
                task_id,
                ev.BLOCKED,
                extra_payload={"failure_reason": failure},
            )

        return PipelineResult(
            success=False,
            task_id=task_id,
            execution_id=execution_id,
            failure_reason=failure,
        )

    async def _ensure_project_on_worker(
        self,
        channel: WorkerChannel,
        project: Project,
    ) -> str:
        """Ensure the worker has the project. Returns the head_commit to use."""
        status_req = GetProjectStatusRequest(
            type="get_project_status",
            request_id=str(uuid4()),
            project_id=str(project.id),
        )
        status_resp = await channel.send_command(status_req)
        assert isinstance(status_resp, GetProjectStatusResponse)

        if not status_resp.exists:
            # SetupProject: bundle the local repo.
            # Send project name as the path hint — the worker resolves it
            # relative to its --workspace directory.
            bundle_b64 = _create_patch_bundle_b64(project.local_path)
            setup_req = SetupProjectRequest(
                type="setup_project",
                request_id=str(uuid4()),
                project_id=str(project.id),
                bundle_b64=bundle_b64,
                path=project.name,
            )
            await channel.send_command(setup_req)
            # Re-query to get head_commit
            status_req2 = GetProjectStatusRequest(
                type="get_project_status",
                request_id=str(uuid4()),
                project_id=str(project.id),
            )
            status_resp2 = await channel.send_command(status_req2)
            assert isinstance(status_resp2, GetProjectStatusResponse)
            return status_resp2.head_commit or "HEAD"
        else:
            local_head = _get_local_head(project.local_path)
            if status_resp.head_commit != local_head:
                # UpdateProject: worker is behind, send a new bundle
                bundle_b64 = _create_patch_bundle_b64(project.local_path)
                update_req = UpdateProjectRequest(
                    type="update_project",
                    request_id=str(uuid4()),
                    project_id=str(project.id),
                    patch_b64=bundle_b64,
                )
                await channel.send_command(update_req)
                return local_head
            return status_resp.head_commit or "HEAD"

    # ------------------------------------------------------------------
    # Implementation pipeline
    # ------------------------------------------------------------------

    async def run_impl_pipeline(
        self,
        channel: WorkerChannel,
        task: Task,
        project: Project,
        spec: Spec,
    ) -> PipelineResult:
        """Drive remote worker through the implementation pipeline.

        Steps: GetProjectStatus → (SetupProject/UpdateProject) → record execution
        → transition to IN_PROGRESS → CreateWorktree → SetupEnvironment
        → RunClaude → GetDiff → RemoveWorktree → transition result.
        """
        task_id = task.id
        project_id = project.id
        execution_id: UUID | None = None

        try:
            # Step 1: ensure worker has the project
            head_commit = await self._ensure_project_on_worker(channel, project)

            # Step 2: record execution start (branch name uses execution_id for consistency)
            exec_uuid = uuid4()
            branch_name = f"execution/{exec_uuid}"
            execution_id = await self._record_execution_start(
                task_id, spec.id, branch_name, execution_id=exec_uuid
            )

            # Step 3: record assignment with real execution_id
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_ASSIGNED_TO_WORKER,
                payload={"worker_id": channel.worker_id, "execution_id": str(execution_id)},
            )

            # Step 4: transition to IN_PROGRESS
            await self._state_machine.transition(
                task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
            )

            # Step 5: CreateWorktree
            create_wt_req = CreateWorktreeRequest(
                type="create_worktree",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
                base_commit=head_commit,
            )
            create_wt_resp = await channel.send_command(create_wt_req)
            assert isinstance(create_wt_resp, CreateWorktreeResponse)
            worktree_path = create_wt_resp.worktree_path or f"/remote/{execution_id}"

            # Step 6: SetupEnvironment — symlink shared deps into worktree
            setup_env_req = SetupEnvironmentRequest(
                type="setup_environment",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
                symlinks=_STANDARD_SYMLINKS,
            )
            await channel.send_command(setup_env_req)

            # Step 7: assemble prompt
            intent_content = ""
            try:
                intent_content = read_intent(project.local_path, project.intent_md)
            except Exception:
                logger.warning("Could not read INTENT.md for task=%s", task_id)

            prompt = build_prompt(intent_content, spec.content)

            # Step 8: RunClaude
            run_claude_req = RunClaudeRequest(
                type="run_claude",
                request_id=str(uuid4()),
                execution_id=str(execution_id),
                prompt=prompt,
                model=WORKER_MODEL,
                tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                cwd=worktree_path,
            )
            claude_resp = await channel.send_command(run_claude_req)
            assert isinstance(claude_resp, RunClaudeResponse)

            # Step 9: save trace and parse output for COMPLETED/BLOCKED markers
            stdout = claude_resp.stdout or ""
            stderr = claude_resp.stderr or ""
            from core.models import ExecutionTrace
            from datetime import UTC, datetime

            session_jsonl = claude_resp.session_jsonl or ""
            trace_content = (
                f"# Execution Trace: {execution_id}\n"
                f"# Task: {task_id}\n"
                f"# Returncode: {claude_resp.returncode}\n\n"
                f"## Claude Output\n{stdout}\n{stderr}\n\n"
                f"## Session Transcript (JSONL)\n{session_jsonl}"
            )
            now = datetime.now(UTC)
            trace = ExecutionTrace(
                execution_id=execution_id,
                task_id=task_id,
                spec_id=spec.id,
                content=trace_content,
                started_at=now,
                created_at=now,
            )
            self._store.save_trace(trace)
            is_blocked = (
                "BLOCKED" in stdout
                or claude_resp.status == "blocked"
                or (claude_resp.returncode is not None and claude_resp.returncode != 0)
            )
            is_completed = "COMPLETED" in stdout and not is_blocked

            # Step 10: GetDiff
            get_diff_req = GetDiffRequest(
                type="get_diff",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
            )
            diff_resp = await channel.send_command(get_diff_req)
            assert isinstance(diff_resp, GetDiffResponse)
            _patch = diff_resp.patch or ""

            # Step 11: RemoveWorktree
            await self._try_remove_worktree(channel, project_id, execution_id)

            # Step 12: transition based on outcome
            if is_completed:
                await self._record_execution_complete(execution_id)
                await self._state_machine.transition(task_id, ev.READY_FOR_QA)
                return PipelineResult(
                    success=True, task_id=task_id, execution_id=execution_id
                )
            else:
                failure = f"Claude returned BLOCKED or non-zero exit: {stdout[:500]}"
                await self._record_execution_fail(execution_id, failure)
                await self._state_machine.transition(
                    task_id,
                    ev.BLOCKED,
                    extra_payload={"failure_reason": failure},
                )
                return PipelineResult(
                    success=False,
                    task_id=task_id,
                    execution_id=execution_id,
                    failure_reason=failure,
                )

        except PipelineAbort as exc:
            logger.error(
                "Impl pipeline aborted for task=%s at step=%s: %s",
                task_id,
                exc.step_name,
                exc.error,
            )
            failure = f"Pipeline aborted at {exc.step_name!r}: {exc.error}"
            return await self._abort_pipeline(
                channel, task_id, project_id, execution_id, failure
            )

    # ------------------------------------------------------------------
    # QA pipeline
    # ------------------------------------------------------------------

    async def run_qa_pipeline(
        self,
        channel: WorkerChannel,
        task: Task,
        project: Project,
        spec: Spec,
    ) -> PipelineResult:
        """Drive remote worker through the QA pipeline.

        Steps: find execution branch → CreateWorktree → SetupEnvironment
        → ReadFile(ratchet.yaml) → RunCommand for each QA step
        → (if failures and retries left: RunClaude fix → re-run)
        → RemoveWorktree → transition result.
        """
        task_id = task.id
        project_id = project.id
        execution_id: UUID | None = None

        # Find execution branch from task events
        execution_events = await self._store.get_events(task_id, "task_executions")
        execution_branch: str | None = None
        for event in reversed(execution_events):
            if event.event_type == ev.EXECUTION_STARTED:
                bn = event.payload.get("branch_name")
                if bn:
                    execution_branch = bn
                break

        if not execution_branch:
            failure = "QA cannot run: no execution branch found for task"
            logger.error("task=%s: %s", task_id, failure)
            await self._state_machine.transition(
                task_id, ev.BLOCKED, extra_payload={"failure_reason": failure}
            )
            return PipelineResult(
                success=False, task_id=task_id, failure_reason=failure
            )

        # Get current QA fix attempts from task events
        task_events = await self._store.get_events(task_id, "task")
        qa_fix_attempts = _get_qa_fix_attempts(task_events)

        try:
            # Step 1: ensure worker has the project
            await self._ensure_project_on_worker(channel, project)

            # Step 2: create execution record for QA
            qa_branch_name = f"qa/{uuid4()}"
            execution_id = await self._record_execution_start(
                task_id, spec.id, qa_branch_name
            )
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_ASSIGNED_TO_WORKER,
                payload={"worker_id": channel.worker_id, "execution_id": str(execution_id)},
            )

            # Step 3: CreateWorktree on execution branch
            create_wt_req = CreateWorktreeRequest(
                type="create_worktree",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
                base_commit=execution_branch,
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

            # Step 5: ReadFile(ratchet.yaml) → parse QA config
            read_file_req = ReadFileRequest(
                type="read_file",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
                path="ratchet.yaml",
            )
            read_file_resp = await channel.send_command(read_file_req)
            assert isinstance(read_file_resp, ReadFileResponse)

            qa_config = None
            if read_file_resp.content:
                qa_config = load_qa_config_from_string(read_file_resp.content)

            if qa_config is None:
                # No QA config — skip QA and advance
                logger.info(
                    "No QA config found for task=%s, transitioning to ready_for_deployment",
                    task_id,
                )
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_complete(execution_id)
                await self._state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)
                return PipelineResult(
                    success=True, task_id=task_id, execution_id=execution_id
                )

            # Step 6: run each QA step via RunCommand
            failed_steps: list[tuple[str, str]] = []
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
                        break  # stop at first failure
                except PipelineAbort as exc:
                    # RunCommand itself failed (transport/executor error)
                    failed_steps.append((step.name, exc.error))
                    break

            if not failed_steps:
                # All QA steps passed
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_complete(execution_id)
                await self._state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)
                return PipelineResult(
                    success=True, task_id=task_id, execution_id=execution_id
                )

            # Some steps failed
            combined_output = "\n\n".join(
                f"Step '{name}':\n{output}" for name, output in failed_steps
            )

            if qa_fix_attempts >= qa_config.max_fix_attempts:
                # Max retries exhausted — block
                logger.info(
                    "Max QA fix attempts reached for task=%s, transitioning to blocked",
                    task_id,
                )
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_fail(execution_id, combined_output)
                await self._state_machine.transition(
                    task_id,
                    ev.BLOCKED,
                    extra_payload={
                        "failure_reason": combined_output,
                        "qa_fix_attempts": qa_fix_attempts,
                    },
                )
                return PipelineResult(
                    success=False,
                    task_id=task_id,
                    execution_id=execution_id,
                    failure_reason=combined_output,
                )

            # Attempt a Claude fix
            fix_prompt = (
                f"{spec.content}\n\n"
                f"QA tools found errors after implementation was marked complete:"
                f"\n{combined_output}"
            )
            logger.info(
                "QA fix attempt %d/%d for task=%s",
                qa_fix_attempts + 1,
                qa_config.max_fix_attempts,
                task_id,
            )
            fix_req = RunClaudeRequest(
                type="run_claude",
                request_id=str(uuid4()),
                execution_id=str(execution_id),
                prompt=fix_prompt,
                model=WORKER_MODEL,
                tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                cwd=worktree_path,
            )
            await channel.send_command(fix_req)

            # Clean up this QA worktree and re-queue for QA
            await self._try_remove_worktree(channel, project_id, execution_id)
            await self._record_execution_fail(
                execution_id, f"QA fix attempt {qa_fix_attempts + 1}, re-queuing"
            )
            await self._state_machine.transition(
                task_id,
                ev.READY_FOR_QA,
                extra_payload={"qa_fix_attempts": qa_fix_attempts + 1},
            )
            return PipelineResult(
                success=False,
                task_id=task_id,
                execution_id=execution_id,
                failure_reason=combined_output,
            )

        except PipelineAbort as exc:
            logger.error(
                "QA pipeline aborted for task=%s at step=%s: %s",
                task_id,
                exc.step_name,
                exc.error,
            )
            failure = f"Pipeline aborted at {exc.step_name!r}: {exc.error}"
            return await self._abort_pipeline(
                channel, task_id, project_id, execution_id, failure
            )

    # ------------------------------------------------------------------
    # Merge pipeline
    # ------------------------------------------------------------------

    async def run_merge_pipeline(
        self,
        channel: WorkerChannel,
        task: Task,
        project: Project,
    ) -> PipelineResult:
        """Drive remote worker through the merge pipeline.

        Steps: local squash merge → CreateWorktree on merge-verify branch
        → SetupEnvironment → run QA steps via RunCommand
        → if pass: advance branch + push → if fail: discard merge
        → RemoveWorktree → transition result.
        """
        task_id = task.id
        project_id = project.id
        execution_id: UUID | None = None
        target_branch = "develop"
        local_path = project.local_path

        # Find execution branch and spec content from task events
        execution_events = await self._store.get_events(task_id, "task_executions")
        execution_branch: str | None = None
        spec_id_val: UUID | None = None
        for event in reversed(execution_events):
            if event.event_type == ev.EXECUTION_STARTED:
                bn = event.payload.get("branch_name")
                si = event.payload.get("spec_id")
                if bn:
                    execution_branch = bn
                    spec_id_val = UUID(si) if si else None
                break

        spec_content = ""
        if spec_id_val is not None:
            spec_events = await self._store.get_events(spec_id_val, "spec")
            for spec_event in spec_events:
                if spec_event.event_type == ev.SPEC_CREATED:
                    spec_content = spec_event.payload.get("content", "")
                    break

        intent_content = ""
        try:
            intent_content = read_intent(local_path, project.intent_md)
        except Exception:
            logger.warning("Could not read INTENT.md for task=%s", task_id)

        if not execution_branch:
            failure = "Merge cannot run: no execution branch found for task"
            logger.error("task=%s: %s", task_id, failure)
            await self._state_machine.transition(
                task_id, ev.BLOCKED, extra_payload={"failure_reason": failure}
            )
            return PipelineResult(
                success=False, task_id=task_id, failure_reason=failure
            )

        # Step 1: perform local squash merge
        merge_result = squash_merge(
            local_path=local_path,
            execution_branch=execution_branch,
            target_branch=target_branch,
            title=task.title,
            task_id=task_id,
            store=self._store,
            invoker=None,
            spec_content=spec_content,
            intent_content=intent_content,
        )

        if not merge_result.success:
            failure = merge_result.failure_reason or "squash merge failed"
            logger.warning("Merge failed for task=%s: %s", task_id, failure)
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

        try:
            # Step 2: ensure worker has the project
            await self._ensure_project_on_worker(channel, project)
            merge_head = merge_result.new_sha or _get_local_head(local_path)

            # Create QA execution record
            qa_branch_name = f"merge-qa/{uuid4()}"
            execution_id = await self._record_execution_start(
                task_id,
                spec_id_val or uuid4(),
                qa_branch_name,
            )
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_ASSIGNED_TO_WORKER,
                payload={"worker_id": channel.worker_id, "execution_id": str(execution_id)},
            )

            # Step 3: CreateWorktree on merge-verify branch
            create_wt_req = CreateWorktreeRequest(
                type="create_worktree",
                request_id=str(uuid4()),
                project_id=str(project_id),
                execution_id=str(execution_id),
                base_commit=merge_head,
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
                # QA passed — RemoveWorktree, push, deploy
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_complete(execution_id)
                # Push the merged branch to remote
                _push_branch(local_path, target_branch)
                await self._state_machine.transition(task_id, ev.DEPLOYED)
                logger.info(
                    "Merge pipeline succeeded for task=%s (sha=%s)",
                    task_id,
                    merge_result.new_sha,
                )
                return PipelineResult(
                    success=True, task_id=task_id, execution_id=execution_id
                )
            else:
                # QA failed — discard merge, block task
                combined_output = "\n\n".join(
                    f"Step '{name}':\n{output}" for name, output in failed_steps
                )
                logger.warning(
                    "Merge QA failed for task=%s: %s", task_id, combined_output[:200]
                )
                _reset_branch(local_path, target_branch)
                await self._try_remove_worktree(channel, project_id, execution_id)
                await self._record_execution_fail(execution_id, combined_output)
                await self._state_machine.transition(
                    task_id,
                    ev.BLOCKED,
                    extra_payload={"failure_reason": combined_output},
                )
                return PipelineResult(
                    success=False,
                    task_id=task_id,
                    execution_id=execution_id,
                    failure_reason=combined_output,
                )

        except PipelineAbort as exc:
            logger.error(
                "Merge pipeline aborted for task=%s at step=%s: %s",
                task_id,
                exc.step_name,
                exc.error,
            )
            failure = f"Pipeline aborted at {exc.step_name!r}: {exc.error}"
            _reset_branch(local_path, target_branch)
            return await self._abort_pipeline(
                channel, task_id, project_id, execution_id, failure
            )


def _get_qa_fix_attempts(task_events: list[Any]) -> int:
    """Read qa_fix_attempts from the latest TASK_STATUS_CHANGED event."""
    for event in reversed(task_events):
        if event.event_type == ev.TASK_STATUS_CHANGED:
            val = event.payload.get("qa_fix_attempts")
            if val is not None:
                return int(val)
    return 0


def _push_branch(local_path: str, branch: str) -> None:
    """Push branch to origin. Logs warning on failure, does not raise."""
    try:
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=local_path,
            capture_output=True,
            check=True,
        )
    except Exception:
        logger.warning("Failed to push branch %s for path=%s", branch, local_path)


def _reset_branch(local_path: str, branch: str) -> None:
    """Reset branch to origin after a failed merge. Best-effort."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=local_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-f", branch, f"origin/{branch}"],
            cwd=local_path,
            capture_output=True,
        )
    except Exception:
        logger.warning(
            "Failed to reset branch %s to origin for path=%s", branch, local_path
        )
