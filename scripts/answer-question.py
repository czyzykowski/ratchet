"""Display and answer a pending question for a task in WAITING_FOR_INPUT status."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Answer a pending question for a task in WAITING_FOR_INPUT status."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID to answer question for")
    return parser.parse_args()


def collect_answer() -> str:
    print("Enter your answer (type '.' on a line by itself or press Ctrl-D to finish):")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == ".":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).rstrip()


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
    from core.qa_manager import get_pending_question
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        sm = TaskStateMachine(store)
        current = await sm.get_current_status(task_id)
        if current is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        if current != ev.WAITING_FOR_INPUT:
            print(
                f"Error: task {task_id} is in status {current!r}, not 'waiting_for_input'.",
                file=sys.stderr,
            )
            sys.exit(1)

        exchange = await get_pending_question(store, task_id)
        if exchange is None:
            print(f"No pending question found for task {task_id}.")
            return

        print(f"\nQuestion [{exchange.question_index}]:")
        print(exchange.question)
        print()

        answer = collect_answer()
        if not answer.strip():
            print("Empty answer — aborting.")
            return

        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_PROVIDED,
            payload={
                "question_index": exchange.question_index,
                "answer": answer,
                "answered_by": "human",
                "execution_id": str(exchange.execution_id),
            },
        )

        await sm.transition(task_id, ev.IN_PROGRESS)

        print(f"Answer recorded. Task {task_id} transitioned back to 'in_progress'.")
    except InvalidTransitionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
