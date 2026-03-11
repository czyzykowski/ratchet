"""Execution manager: worktree lifecycle and execution event management."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from core import events as ev
from core.models import Event, Execution, Project
from core.store import Store

logger = logging.getLogger(__name__)


def prepare_task_environment(
    repo_path: str, execution_id: UUID, claude_md: str | None = None
) -> str:
    """Create git worktree on a named branch for execution.

    Worktree path: <repo_path>/.worktrees/<execution_id>
    Branch name: execution/<execution_id>
    Runs: git worktree add <worktree_path> -b execution/<execution_id> HEAD
    If claude_md is not None, writes it to <worktree_path>/CLAUDE.md.
    Returns worktree_path on success.
    Raises subprocess.CalledProcessError on failure.
    """
    worktree_path = os.path.join(repo_path, ".worktrees", str(execution_id))
    branch_name = f"execution/{execution_id}"
    subprocess.run(
        ["git", "worktree", "add", worktree_path, "-b", branch_name, "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    if claude_md is not None:
        Path(worktree_path, "CLAUDE.md").write_text(claude_md)
    return worktree_path


def cleanup_task_environment(repo_path: str, execution_id: UUID) -> None:
    """Remove git worktree directory. Branch is preserved intentionally.

    Runs: git worktree remove --force <worktree_path>
    Does NOT delete branch execution/<execution_id>.
    Logs warning on failure but does not raise.
    """
    worktree_path = os.path.join(repo_path, ".worktrees", str(execution_id))
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
    except Exception:
        logger.warning(
            "Failed to remove worktree for execution %s at %s",
            execution_id,
            worktree_path,
            exc_info=True,
        )


def _build_execution(execution_events: list[Event]) -> Execution | None:
    """Build an Execution model by replaying events for a single execution."""
    started_event: Event | None = None
    final_event: Event | None = None

    for event in execution_events:
        if event.event_type == ev.EXECUTION_STARTED:
            started_event = event
        elif event.event_type in (ev.EXECUTION_COMPLETED, ev.EXECUTION_FAILED):
            final_event = event

    if started_event is None:
        return None

    p = started_event.payload
    execution_id = UUID(p["execution_id"])
    task_id = UUID(p["task_id"])
    spec_id = UUID(p["spec_id"])
    started_at = started_event.occurred_at

    branch_name = p.get("branch_name")

    if final_event is None:
        return Execution(
            id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            status="running",
            failure_reason=None,
            branch_name=branch_name,
            started_at=started_at,
            completed_at=None,
        )

    fp = final_event.payload
    return Execution(
        id=execution_id,
        task_id=task_id,
        spec_id=spec_id,
        status=fp["status"],
        failure_reason=fp.get("failure_reason"),
        branch_name=branch_name,
        started_at=started_at,
        completed_at=final_event.occurred_at,
    )


class ExecutionManager:
    def __init__(self, store: Store, repo_path: str) -> None:
        self._store = store
        self._repo_path = repo_path

    async def start_execution(
        self, task_id: UUID, spec_id: UUID, project: Project | None = None
    ) -> Execution:
        """Prepare environment and record execution start.

        1. Generate new execution_id
        2. Call prepare_task_environment(repo_path, execution_id, claude_md)
           - claude_md is taken from project.claude_md when project.config_source == "db"
           - If it raises, append EXECUTION_FAILED with reason and raise EnvironmentError
        3. Append EXECUTION_STARTED event
        4. Return Execution model with status 'running'
        """
        execution_id = uuid4()
        claude_md: str | None = None
        if project is not None and project.config_source == "db":
            claude_md = project.claude_md

        try:
            worktree_path = prepare_task_environment(self._repo_path, execution_id, claude_md)
        except Exception as exc:
            failure_reason = f"Failed to prepare environment: {exc}"
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
            raise OSError(failure_reason) from exc

        branch_name = f"execution/{execution_id}"
        payload = {
            "execution_id": str(execution_id),
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "worktree_path": worktree_path,
            "branch_name": branch_name,
            "status": "running",
        }
        event = await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_STARTED,
            payload=payload,
        )
        # Record under task_id so get_execution_history(task_id) can find it.
        await self._store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload=payload,
        )

        return Execution(
            id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            status="running",
            failure_reason=None,
            branch_name=branch_name,
            started_at=event.occurred_at,
            completed_at=None,
        )

    async def complete_execution(self, execution_id: UUID) -> Event:
        """Record successful completion and clean up environment.

        1. Append EXECUTION_COMPLETED event
        2. Call cleanup_task_environment(repo_path, execution_id)
           - Log cleanup errors but do not raise
        3. Return the appended event
        """
        event = await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_COMPLETED,
            payload={
                "execution_id": str(execution_id),
                "status": "completed",
            },
        )
        try:
            cleanup_task_environment(self._repo_path, execution_id)
        except Exception:
            logger.warning(
                "Failed to clean up worktree for execution %s", execution_id, exc_info=True
            )
        return event

    async def fail_execution(self, execution_id: UUID, failure_reason: str) -> Event:
        """Record failure and clean up environment.

        1. Append EXECUTION_FAILED event with failure_reason
        2. Call cleanup_task_environment(repo_path, execution_id)
           - Log cleanup errors but do not raise
        3. Return the appended event
        """
        event = await self._store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_FAILED,
            payload={
                "execution_id": str(execution_id),
                "failure_reason": failure_reason,
                "status": "failed",
            },
        )
        try:
            cleanup_task_environment(self._repo_path, execution_id)
        except Exception:
            logger.warning(
                "Failed to clean up worktree for execution %s", execution_id, exc_info=True
            )
        return event

    async def get_current_execution(self, task_id: UUID) -> Execution | None:
        """Return active execution for task (status == 'running'), or None.

        Derived by replaying EXECUTION_STARTED and EXECUTION_COMPLETED/FAILED events.
        """
        executions = await self.get_execution_history(task_id)
        for execution in executions:
            if execution.status == "running":
                return execution
        return None

    async def get_execution_history(self, task_id: UUID) -> list[Execution]:
        """Return all executions for task ordered by started_at ascending."""
        task_events = await self._store.get_events(task_id, "task_executions")

        # Collect execution_ids in the order they first appeared.
        execution_ids_ordered: list[UUID] = []
        seen: set[UUID] = set()
        for event in task_events:
            eid = UUID(event.payload["execution_id"])
            if eid not in seen:
                execution_ids_ordered.append(eid)
                seen.add(eid)

        executions: list[Execution] = []
        for execution_id in execution_ids_ordered:
            execution_events = await self._store.get_events(execution_id, "execution")
            execution = _build_execution(execution_events)
            if execution is not None:
                executions.append(execution)

        executions.sort(key=lambda e: e.started_at)
        return executions
