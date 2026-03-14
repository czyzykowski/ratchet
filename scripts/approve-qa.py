"""Advance task from ready_for_qa to ready_for_merge."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approve QA for a task, advancing it to ready_for_merge."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID")
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
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        state_machine = TaskStateMachine(store)

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

        if current_status != ev.READY_FOR_QA:
            print(
                f"Error: task must be in ready_for_qa status, got {current_status!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print("Task advanced to ready_for_merge")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
