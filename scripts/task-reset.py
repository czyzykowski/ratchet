"""Reset task backwards for re-execution."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

_TERMINAL_STATUSES = {"merged"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset a task backwards for re-execution.")
    parser.add_argument("--task-id", required=True, help="Task UUID")
    parser.add_argument(
        "--reuse-spec",
        action="store_true",
        help="Reset to ready_for_implementation using the last assigned spec",
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
    from core.spec_manager import SpecManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        state_machine = TaskStateMachine(store)
        spec_manager = SpecManager(store)

        current_status = await state_machine.get_current_status(task_id)
        if current_status is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        task_events = await store.get_events(task_id, "task")
        title = ""
        for event in task_events:
            if event.event_type == ev.TASK_CREATED:
                title = event.payload.get("title", "")
                break

        print(f"Task: {title}")
        print(f"Status: {current_status}")

        if current_status in _TERMINAL_STATUSES:
            print(
                f"Error: cannot reset task in terminal status {current_status!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.reuse_spec:
            current_spec = await spec_manager.get_current_spec(task_id)
            if current_spec is None:
                print(
                    "Error: no spec has been assigned to this task.",
                    file=sys.stderr,
                )
                sys.exit(1)

            try:
                await state_machine.transition(task_id, ev.READY_FOR_SPEC)
                await spec_manager.assign_spec(task_id, current_spec.id)
                await state_machine.transition(task_id, ev.SPEC_QA)
                await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
            except InvalidTransitionError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)

            print("Task reset to ready_for_implementation")
        else:
            try:
                await state_machine.transition(task_id, ev.READY_FOR_SPEC)
            except InvalidTransitionError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)

            print("Task reset to ready_for_spec")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
