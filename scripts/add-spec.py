"""Create a spec, assign it to a task, and advance task to ready_for_implementation."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assign a spec file to a task and advance it to ready_for_implementation."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID")
    parser.add_argument("--file", required=True, help="Path to spec file")
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

    try:
        with open(args.file) as f:
            content = f.read()
    except OSError as exc:
        print(f"Error reading spec file: {exc}", file=sys.stderr)
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

        allowed = {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION}
        if current_status not in allowed:
            print(
                f"Error: Cannot assign spec to task in status {current_status!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        current_spec = await spec_manager.get_current_spec(task_id)
        previous_spec_id = current_spec.id if current_spec is not None else None

        spec = await spec_manager.create_spec(task_id, content, previous_spec_id)
        await spec_manager.assign_spec(task_id, spec.id)

        try:
            if current_status == ev.READY_FOR_SPEC:
                await state_machine.transition(task_id, ev.SPEC_QA)
                await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
            elif current_status in (ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION):
                await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Created spec: {spec.id}")
        print(f"Assigned to task: {task_id}")
        print(f"Task status: {ev.READY_FOR_IMPLEMENTATION}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
