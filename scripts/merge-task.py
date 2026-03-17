"""Squash merge execution branch and advance task to merged."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Squash merge execution branch and advance task to merged."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID")
    parser.add_argument(
        "--branch",
        default="develop",
        help="Target branch to merge into (default: develop)",
    )
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Skip all git operations and advance task directly to merged status",
    )
    parser.add_argument(
        "--skip-merge-hooks",
        action="store_true",
        help="Skip merge hook execution entirely",
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    try:
        task_id = UUID(args.task_id)
    except ValueError:
        print(f"Error: invalid task-id: {args.task_id!r}", file=sys.stderr)
        sys.exit(1)

    from core import events as ev
    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        state_machine = TaskStateMachine(store)
        project_manager = ProjectManager(store)

        current_status = await state_machine.get_current_status(task_id)
        if current_status is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        if current_status != ev.READY_FOR_DEPLOYMENT:
            print(
                f"Error: task must be in ready_for_merge status, got {current_status!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        task_events = await store.get_events(task_id, "task")
        title = ""
        project_id = None
        for event in task_events:
            if event.event_type == ev.TASK_CREATED:
                title = event.payload.get("title", "")
                pid_str = event.payload.get("project_id")
                if pid_str:
                    project_id = UUID(pid_str)
                break

        if project_id is None:
            print("Error: could not determine project_id for task.", file=sys.stderr)
            sys.exit(1)

        project = await project_manager.get_project(project_id)
        if project is None:
            print(f"Error: project {project_id} not found.", file=sys.stderr)
            sys.exit(1)

        if not args.skip_merge:
            execution_events = await store.get_events(task_id, "task_executions")
            branch_name = None
            spec_id = None
            for event in reversed(execution_events):
                if event.event_type == ev.EXECUTION_STARTED:
                    bn = event.payload.get("branch_name")
                    si = event.payload.get("spec_id")
                    if bn:
                        branch_name = bn
                        spec_id = UUID(si) if si else None
                        break

            if branch_name is None:
                print(
                    "Error: branch_name not found in execution events for this task.",
                    file=sys.stderr,
                )
                sys.exit(1)

            local_path = project.local_path
            target_branch = args.branch

            from core.context_assembler import read_intent
            from core.invoker import ClaudeCodeInvoker
            from core.merge import squash_merge

            spec_content = ""
            if spec_id is not None:
                spec_events = await store.get_events(spec_id, "spec")
                for spec_event in spec_events:
                    if spec_event.event_type == ev.SPEC_CREATED:
                        spec_content = spec_event.payload.get("content", "")
                        break

            intent_content = read_intent(local_path)
            merge_result = squash_merge(
                local_path=local_path,
                execution_branch=branch_name,
                target_branch=target_branch,
                title=title,
                task_id=task_id,
                store=store,
                invoker=ClaudeCodeInvoker(store=store),
                spec_content=spec_content,
                intent_content=intent_content,
            )

            if not merge_result.success:
                failure_reason = merge_result.failure_reason or "merge failed"
                print(f"Error: {failure_reason}", file=sys.stderr)
                try:
                    await state_machine.transition(
                        task_id,
                        ev.BLOCKED,
                        extra_payload={"failure_reason": failure_reason},
                    )
                except InvalidTransitionError as exc:
                    print(f"Error transitioning to blocked: {exc}", file=sys.stderr)
                sys.exit(1)

        from core.qa_runner import load_merge_config, run_merge_steps

        hook_summary: str
        if args.skip_merge_hooks:
            hook_summary = "(merge hooks skipped)"
        else:
            merge_config = load_merge_config(project.local_path)
            if merge_config is None or not merge_config.steps:
                hook_summary = "(no merge hooks)"
            else:
                hook_results = run_merge_steps(merge_config, project.local_path)
                failed_count = sum(1 for r in hook_results if r.returncode != 0)
                await store.append_event(
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
                hook_summary = f"({len(hook_results)} merge hooks run, {failed_count} failed)"

        try:
            await state_machine.transition(task_id, ev.DEPLOYED)
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.skip_merge:
            print(f"Merged task {title!r} — skipped git merge {hook_summary}")
        else:
            print(f"Merged task {title!r} — merged to {args.branch} {hook_summary}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
