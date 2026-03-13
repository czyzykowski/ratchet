"""Interactive conversational spec creation script."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from core.store import Store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively create a spec for a task via conversation with Claude."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID")
    return parser.parse_args()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")




def extract_spec(output: str) -> str:
    marker = "## SPEC READY"
    idx = output.find(marker)
    if idx == -1:
        return ""
    return output[idx + len(marker) :].strip()


async def load_task_info(
    task_id: UUID, store: Store
) -> tuple[str | None, str | None, UUID | None]:
    """Return (current_status, task_title, project_id) from event replay."""
    from core import events as ev

    task_events = await store.get_events(task_id, "task")
    status: str | None = None
    task_title: str | None = None
    project_id: UUID | None = None

    for event in task_events:
        if event.event_type == ev.TASK_CREATED:
            status = event.payload.get("status", ev.READY_FOR_SPEC)
            task_title = event.payload.get("title", "")
            project_id = UUID(event.payload["project_id"])
        elif event.event_type == ev.TASK_STATUS_CHANGED:
            status = event.payload["to_status"]

    return status, task_title, project_id


async def assign_spec_to_task(task_id: UUID, spec_content: str, store: Store) -> None:
    """Create and assign spec, then advance task status."""
    from core import events as ev
    from core.spec_manager import SpecManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine

    state_machine = TaskStateMachine(store)
    spec_manager = SpecManager(store)

    current_status = await state_machine.get_current_status(task_id)
    current_spec = await spec_manager.get_current_spec(task_id)
    previous_spec_id = current_spec.id if current_spec is not None else None

    spec = await spec_manager.create_spec(task_id, spec_content, previous_spec_id)
    await spec_manager.assign_spec(task_id, spec.id)

    try:
        if current_status == ev.READY_FOR_SPEC:
            await state_machine.transition(task_id, ev.SPEC_QA)
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        elif current_status in (ev.SPEC_QA, ev.BLOCKED, ev.READY_FOR_IMPLEMENTATION):
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    except InvalidTransitionError as exc:
        print(f"Error transitioning task status: {exc}", file=sys.stderr)
        raise


async def _handle_spec_ready(
    output: str,
    task_id: UUID,
    task_title: str | None,
    store: Store,
) -> bool:
    """Check for ## SPEC READY marker and handle save confirmation.

    Returns True if spec was saved, False if marker absent or user declined.
    """
    if "## SPEC READY" not in output:
        return False

    spec_content = extract_spec(output)

    try:
        confirm = input("\nSave and assign this spec? [y/n] ").strip().lower()
    except KeyboardInterrupt:
        print("\nSession ended, spec not saved.")
        sys.exit(0)

    if confirm != "y":
        return False

    short_title = slugify(task_title[:40]) if task_title else "spec"
    short_id = str(task_id)[:8]
    filename = f"{short_title}-{short_id}.md"
    specs_dir = Path("specs")
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / filename
    spec_file.write_text(spec_content)

    await assign_spec_to_task(task_id, spec_content, store)
    print(f"Spec saved to specs/{filename} and assigned to task")
    return True


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

    if not shutil.which("claude"):
        print("Error: claude binary not found in PATH.", file=sys.stderr)
        sys.exit(1)

    from core import events as ev
    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        current_status, task_title, project_id = await load_task_info(task_id, store)

        if current_status is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        if current_status != ev.READY_FOR_SPEC:
            print(
                f"Error: task {task_id} is in status {current_status!r},"
                f" expected ready_for_spec.",
                file=sys.stderr,
            )
            sys.exit(1)

        if project_id is None:
            print(f"Error: task {task_id} has no project.", file=sys.stderr)
            sys.exit(1)

        pm = ProjectManager(store)
        project = await pm.get_project(project_id)
        if project is None:
            print(f"Error: project {project_id} not found.", file=sys.stderr)
            sys.exit(1)

        local_path = project.local_path
        if project.config_source == "db":
            intent_md = project.intent_md or ""
        else:
            intent_md_path = Path(local_path) / "docs" / "INTENT.md"
            if not intent_md_path.exists():
                print(f"Error: INTENT.md not found at {intent_md_path}", file=sys.stderr)
                sys.exit(1)
            intent_md = intent_md_path.read_text()

        print(f"Task: {task_title}")
        print(f"Project: {project.name} ({local_path})")

        from core.claude_repl import SpecReplSession
        from web.routes.api.tasks import _build_initial_spec_prompt

        system_prompt = _build_initial_spec_prompt(
            intent_md, task_title or "", task_title or ""
        )
        session = SpecReplSession(
            task_id=str(task_id),
            system_prompt=system_prompt,
            cwd=str(local_path),
        )

        try:
            print("\n--- Claude ---")
            output = ""
            async for chunk in session.ask(task_title or ""):
                print(chunk, end="", flush=True)
                output += chunk
            print()

            if await _handle_spec_ready(output, task_id, task_title, store):
                return

            while True:
                try:
                    user_input = input("\n> ").strip()
                except KeyboardInterrupt:
                    print("\nSession ended, spec not saved.")
                    sys.exit(0)

                if not user_input:
                    continue

                print("\n--- Claude ---")
                output = ""
                async for chunk in session.ask(user_input):
                    print(chunk, end="", flush=True)
                    output += chunk
                print()

                if await _handle_spec_ready(output, task_id, task_title, store):
                    break
        finally:
            await session.close()

    except KeyboardInterrupt:
        print("\nSession ended, spec not saved.")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
