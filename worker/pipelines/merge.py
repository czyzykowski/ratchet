"""MergePipeline: squash-merge execution branch, run deploy hooks, poll PRs."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from worker.dispatcher import DispatchResult

from core import events as ev
from core.context_assembler import read_intent
from core.invoker import ClaudeCodeInvoker
from core.managers import Managers
from core.merge import squash_merge
from core.models import Task
from core.qa_runner import (
    load_deployment_config,
    load_merge_config,
    run_merge_steps,
)
from worker.capability_check import capabilities_met
from worker.event_helpers import gh_command
from worker.task_finder import TaskFinder

logger = logging.getLogger(__name__)


class MergePipeline:
    def __init__(
        self,
        managers: Managers,
        invoker: ClaudeCodeInvoker,
        task_finder: TaskFinder,
        capabilities: list[str] | None = None,
    ) -> None:
        self._store = managers.store
        self._invoker = invoker
        self._task_finder = task_finder
        self._state_machine = managers.state_machine
        self._capabilities = capabilities or []

    async def run(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass auto-merge for local deployment mode."""
        from worker.dispatcher import DispatchResult  # runtime import to avoid circular

        def _no_auto_merge_failed(task: Task, task_events: list[Any]) -> bool:
            return not any(
                e.event_type == ev.TASK_AUTO_MERGE_FAILED for e in task_events
            )

        candidates = await self._task_finder.find(
            statuses={ev.READY_FOR_DEPLOYMENT},
            project_id=project_id,
            predicate=_no_auto_merge_failed,
        )

        # Post-filter: deployment mode, execution branch, capabilities
        merge_candidates: list[tuple[Task, str, str, str, str]] = []
        for task, project, _ in candidates:
            if not capabilities_met(task, project, self._capabilities):
                continue
            ratchet_yaml = (
                project.ratchet_yaml if project.config_source == "db" else None
            )
            deployment_config = load_deployment_config(
                project.local_path, ratchet_yaml
            )
            if deployment_config.mode != "local":
                continue

            execution_events = await self._store.get_events(
                task.id, "task_executions"
            )
            branch_name: str | None = None
            spec_id_val: UUID | None = None
            for event in reversed(execution_events):
                if event.event_type == ev.EXECUTION_STARTED:
                    bn = event.payload.get("branch_name")
                    si = event.payload.get("spec_id")
                    if bn:
                        branch_name = bn
                        spec_id_val = UUID(si) if si else None
                        break

            if branch_name is None:
                logger.warning(
                    "merge_once: task=%s has no execution branch, skipping",
                    task.id,
                )
                continue

            spec_content = ""
            if spec_id_val is not None:
                spec_events = await self._store.get_events(spec_id_val, "spec")
                for spec_event in spec_events:
                    if spec_event.event_type == ev.SPEC_CREATED:
                        spec_content = spec_event.payload.get("content", "")
                        break

            merge_candidates.append(
                (
                    task,
                    project.local_path,
                    deployment_config.base_branch,
                    branch_name,
                    spec_content,
                )
            )

        if not merge_candidates:
            logger.info("merge_once: no eligible tasks for auto-merge.")
            return DispatchResult(action="idle")

        merge_candidates.sort(key=lambda c: c[0].created_at)
        task_m, local_path, target_branch, execution_branch, spec_content = (
            merge_candidates[0]
        )
        task_id = task_m.id

        try:
            intent_content = read_intent(local_path)
        except Exception as exc:
            logger.warning(
                "merge_once: could not read INTENT.md for task=%s: %s",
                task_id,
                exc,
            )
            intent_content = ""

        logger.info(
            "merge_once: merging task=%s branch=%s into %s",
            task_id,
            execution_branch,
            target_branch,
        )
        merge_result = await asyncio.to_thread(
            squash_merge,
            local_path,
            execution_branch,
            target_branch,
            task_m.title,
            task_id,
            self._store,
            self._invoker,
            spec_content,
            intent_content,
        )

        if not merge_result.success:
            failure_reason = merge_result.failure_reason or "merge failed"
            logger.warning(
                "merge_once: merge failed for task=%s: %s", task_id, failure_reason
            )
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_AUTO_MERGE_FAILED,
                payload={"failure_reason": failure_reason},
            )
            return DispatchResult(
                action="merge", task_id=task_id, success=False, detail=failure_reason
            )

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

        await self._state_machine.transition(task_id, ev.DEPLOYED)
        logger.info(
            "merge_once: task=%s deployed (merge_commit_sha=%s)",
            task_id,
            merge_result.new_sha,
        )
        return DispatchResult(action="merge", task_id=task_id)

    async def poll_prs(self) -> None:
        """Poll GitHub for merged PRs and transition tasks to deployed."""
        candidates = await self._task_finder.find(statuses={ev.READY_FOR_DEPLOYMENT})

        for task, project, _ in candidates:
            task_events = await self._store.get_events(task.id, "task")
            pr_number: int | None = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_PR_CREATED:
                    pr_number = event.payload.get("pr_number")
                    break

            if pr_number is None:
                continue

            cwd = project.local_path or os.getcwd()
            _pr_num = int(pr_number)
            _cwd = str(cwd)
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: gh_command(
                    ["pr", "view", str(_pr_num), "--json", "state,mergeCommit"],
                    _cwd,
                ),
            )
            if proc.returncode != 0:
                logger.warning(
                    "gh pr view failed for task=%s pr=%s: %s",
                    task.id,
                    pr_number,
                    proc.stderr.strip(),
                )
                continue

            try:
                import json as _json

                state_data = _json.loads(proc.stdout)
                pr_state = state_data.get("state", "")
            except Exception:
                logger.warning(
                    "Failed to parse gh pr view output for task=%s", task.id
                )
                continue

            if pr_state == "MERGED":
                logger.info(
                    "PR %s merged — transitioning task=%s to deployed",
                    pr_number,
                    task.id,
                )
                sha = (state_data.get("mergeCommit") or {}).get("oid") or None
                extra_payload = {"merge_commit_sha": sha} if sha else None
                await self._state_machine.transition(
                    task.id, ev.DEPLOYED, extra_payload=extra_payload
                )
