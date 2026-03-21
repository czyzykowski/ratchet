"""QAPipeline: run QA steps, auto-fix attempts, Claude review on execution branch."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from worker.dispatcher import DispatchResult

from core import events as ev
from core.claude_subprocess import ClaudeRequest
from core.claude_subprocess import run as run_claude_subprocess
from core.context_assembler import ExecutionContext
from core.invoker import ClaudeCodeInvoker
from core.managers import Managers
from core.models import Project, Spec, Task
from core.models_config import WORKER_MODEL
from core.qa_runner import (
    build_review_prompt,
    get_git_diff,
    load_qa_config,
    parse_review_output,
    run_auto_fixes,
    run_qa_steps,
)
from worker.event_helpers import get_qa_fix_attempts
from worker.task_finder import TaskFinder
from worker.worktree import (
    QAWorktreeError,
    create_qa_worktree,
    remove_qa_worktree,
)

logger = logging.getLogger(__name__)


class QAPipeline:
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
        self._spec_manager = managers.specs
        self._state_machine = managers.state_machine
        self._capabilities = capabilities or []

    async def run(self, project_id: UUID | None = None) -> DispatchResult:
        from worker.dispatcher import DispatchResult  # runtime import to avoid circular

        candidates = await self._task_finder.find(
            statuses={ev.READY_FOR_QA},
            project_id=project_id,
            predicate=lambda t, _: set(t.required_capabilities).issubset(
                set(self._capabilities)
            ),
        )

        task: Task | None = None
        project: Project | None = None
        spec: Spec | None = None
        for t, p, _ in candidates:
            s = await self._spec_manager.get_current_spec(t.id)
            if s is None:
                logger.warning("Task %s has no spec assigned, skipping", t.id)
                continue
            task, project, spec = t, p, s
            break

        if task is None or project is None or spec is None:
            logger.info("No QA tasks ready.")
            return DispatchResult(action="idle")

        # Load QA config
        qa_ratchet_yaml = (
            project.ratchet_yaml if project.config_source == "db" else None
        )
        config = load_qa_config(project.local_path, qa_ratchet_yaml)
        if config is None:
            logger.info(
                "No QA config found for task=%s, transitioning to ready_for_merge",
                task.id,
            )
            await self._state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
            return DispatchResult(action="qa", task_id=task.id)

        # Look up execution branch
        execution_events = await self._store.get_events(task.id, "task_executions")
        execution_branch: str | None = None
        for event in reversed(execution_events):
            if event.event_type == ev.EXECUTION_STARTED:
                bn = event.payload.get("branch_name")
                if bn:
                    execution_branch = bn
                break

        if not execution_branch:
            failure_reason = "QA cannot run: no execution branch found for task"
            logger.error("task=%s: %s", task.id, failure_reason)
            await self._state_machine.transition(
                task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
            )
            return DispatchResult(action="qa", task_id=task.id, success=False)

        try:
            qa_path, qa_worktree_owned = await asyncio.to_thread(
                create_qa_worktree, project.local_path, execution_branch
            )
        except QAWorktreeError as exc:
            failure_reason = f"QA worktree creation failed: {exc}"
            logger.error("task=%s: %s", task.id, failure_reason)
            await self._state_machine.transition(
                task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
            )
            return DispatchResult(action="qa", task_id=task.id, success=False)

        try:
            await asyncio.to_thread(run_auto_fixes, config, qa_path)
            step_results = await asyncio.to_thread(run_qa_steps, config, qa_path)

            failed_steps = [r for r in step_results if r.returncode != 0]

            if failed_steps:
                task_events = await self._store.get_events(task.id, "task")
                qa_fix_attempts_count = get_qa_fix_attempts(task_events)

                if qa_fix_attempts_count >= config.max_fix_attempts:
                    combined_output = "\n\n".join(
                        f"Step '{r.step_name}':\n{r.output}" for r in failed_steps
                    )
                    logger.info(
                        "Max fix attempts reached for task=%s, transitioning to blocked",
                        task.id,
                    )
                    await self._state_machine.transition(
                        task.id,
                        ev.BLOCKED,
                        extra_payload={
                            "failure_reason": combined_output,
                            "qa_fix_attempts": qa_fix_attempts_count,
                        },
                    )
                    return DispatchResult(action="qa", task_id=task.id)

                failed_output = "\n\n".join(
                    f"Step '{r.step_name}' (exit {r.returncode}):\n{r.output}"
                    for r in failed_steps
                )
                fix_prompt = (
                    f"{spec.content}\n\n"
                    f"QA tools found errors after implementation was marked complete:"
                    f"\n{failed_output}"
                )
                fix_context = ExecutionContext(
                    execution_id=uuid4(),
                    task_id=task.id,
                    spec_id=spec.id,
                    worktree_path=qa_path,
                    prompt=fix_prompt,
                )
                logger.info(
                    "Auto-fix attempt %d/%d for task=%s",
                    qa_fix_attempts_count + 1,
                    config.max_fix_attempts,
                    task.id,
                )
                await asyncio.to_thread(self._invoker.invoke, fix_context)
                await self._state_machine.transition(
                    task.id,
                    ev.READY_FOR_QA,
                    extra_payload={"qa_fix_attempts": qa_fix_attempts_count + 1},
                )
                return DispatchResult(action="qa", task_id=task.id)
        finally:
            if qa_worktree_owned:
                await asyncio.to_thread(
                    remove_qa_worktree, project.local_path, qa_path
                )

        # All steps pass — run Claude review
        diff = get_git_diff(project.local_path, execution_branch)
        review_prompt = build_review_prompt(spec.content, diff, step_results)

        review_request = ClaudeRequest(
            prompt=review_prompt,
            cwd=project.local_path,
            model=WORKER_MODEL,
            allowed_tools="Bash,Read,Glob,Grep",
        )
        review_claude_result = await asyncio.to_thread(run_claude_subprocess, review_request)
        review_output = review_claude_result.output

        review_result = parse_review_output(review_output)

        if review_result.verdict == "passed":
            logger.info("QA review passed for task=%s", task.id)
            await self._state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
        else:
            logger.info("QA review failed for task=%s", task.id)
            await self._state_machine.transition(
                task.id,
                ev.BLOCKED,
                extra_payload={"failure_reason": review_result.full_output},
            )

        return DispatchResult(action="qa", task_id=task.id)
