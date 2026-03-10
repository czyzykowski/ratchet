"""Batch compile eligible high-level specs into tasks."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from core.feature_manager import FeatureManager
    from core.models import HighLevelSpec
    from core.store import Store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile eligible high-level specs into tasks for a feature."
    )
    parser.add_argument("--feature-id", required=True, help="Feature UUID")
    return parser.parse_args()


def run_claude(prompt: str, local_path: str, debug: bool = False) -> str:
    """Run claude -p non-interactively, stream output, and return full output."""
    if debug:
        print("\n--- DEBUG: PROMPT SENT TO CLAUDE ---", file=sys.stderr)
        print(prompt, file=sys.stderr)
        print("--- END PROMPT ---\n", file=sys.stderr)

    cmd = ["claude", "-p", prompt, "--allowedTools", "Read,Glob,Bash"]
    proc = subprocess.Popen(
        cmd,
        cwd=local_path,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )

    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)

    proc.wait()
    return "".join(output_lines)


def build_compile_prompt(intent_md: str, feature_title: str, hls: HighLevelSpec) -> str:
    """Build the non-interactive compilation prompt for a single high-level spec."""
    return f"""You are generating a detailed implementation spec for a software task.

## Project Intent
{intent_md}

## Feature
{feature_title}

## High-Level Spec to Compile
Title: {hls.title}

{hls.content}

## Instructions
Read the codebase to understand current patterns and conventions.
Then produce a complete, detailed implementation spec in this exact format,
preceded by '## SPEC READY' on its own line:

## SPEC READY
# Spec N: <title>

## Objective
...

## Success Criteria
- [ ] ...

## Out of Scope
...

## Technical Context
...

## Tasks
- [ ] ...

## Assumptions
...

## Verification Commands
```bash
...
```

## What Exists After This Spec

...

Be specific about file paths, function names, and test requirements.
Do not ask clarifying questions — produce the spec directly."""


def extract_spec(output: str) -> str:
    marker = "## SPEC READY"
    idx = output.find(marker)
    if idx == -1:
        return ""
    return output[idx + len(marker) :].strip()


async def get_task_status(task_id: UUID, store: Store) -> str | None:
    """Get current task status by replaying events."""
    from core import events as ev

    task_events = await store.get_events(task_id, "task")
    status: str | None = None
    for event in task_events:
        if event.event_type == ev.TASK_CREATED:
            status = event.payload.get("status", ev.READY_FOR_SPEC)
        elif event.event_type == ev.TASK_STATUS_CHANGED:
            status = event.payload["to_status"]
    return status


async def is_eligible(hls: HighLevelSpec, fm: FeatureManager, store: Store) -> bool:
    """Check whether a high-level spec is eligible for compilation.

    A spec is eligible when:
    - compiled == False
    - for every UUID in dependencies, the corresponding HighLevelSpec.task_id is not None
      and that task's status is deployed
    """
    if hls.compiled:
        return False

    if not hls.dependencies:
        return True

    # Load all specs in the feature to resolve dependency task_ids
    all_specs = await fm.get_high_level_specs(hls.feature_id)
    specs_by_id = {s.id: s for s in all_specs}

    from core import events as ev

    for dep_id in hls.dependencies:
        dep_spec = specs_by_id.get(dep_id)
        if dep_spec is None or dep_spec.task_id is None:
            return False
        dep_task_status = await get_task_status(dep_spec.task_id, store)
        if dep_task_status != ev.DEPLOYED:
            return False

    return True


async def compile_hls(
    hls: HighLevelSpec,
    feature_title: str,
    project_id: UUID,
    intent_md: str,
    local_path: str,
    store: Store,
    fm: FeatureManager,
    debug: bool = False,
) -> bool:
    """Compile a single high-level spec into a task.

    Returns True on success, False on failure.
    """
    from core import events as ev
    from core.spec_manager import SpecManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine

    print(f"\n[Compiling] {hls.title} (order={hls.order})")

    prompt = build_compile_prompt(intent_md, feature_title, hls)
    output = run_claude(prompt, local_path, debug)

    spec_content = extract_spec(output)
    if not spec_content:
        print("  ERROR: ## SPEC READY marker not found in Claude output.", file=sys.stderr)
        return False

    # Create task
    task_id = uuid4()
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": hls.title,
            "status": ev.READY_FOR_SPEC,
        },
    )
    # Dual-write to project_tasks registry
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": hls.title,
        },
    )

    # Create and assign spec, advance task to ready_for_implementation
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    spec = await spec_manager.create_spec(task_id, spec_content)
    await spec_manager.assign_spec(task_id, spec.id)

    try:
        await state_machine.transition(task_id, ev.SPEC_QA)
        await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    except InvalidTransitionError as exc:
        print(f"  ERROR: state transition failed: {exc}", file=sys.stderr)
        return False

    # Mark the high-level spec as compiled
    await fm.mark_compiled(hls.id, task_id, hls.feature_id)

    print(f"  Created task: {task_id}")
    print(f"  Task status: {ev.READY_FOR_IMPLEMENTATION}")
    return True


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    debug = bool(os.environ.get("RATCHET_DEBUG"))

    try:
        feature_id = UUID(args.feature_id)
    except ValueError:
        print(f"Error: invalid feature-id: {args.feature_id!r}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("claude"):
        print("Error: claude binary not found in PATH.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.feature_manager import FeatureManager
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        fm = FeatureManager(store)

        feature = await fm.get_feature(feature_id)
        if feature is None:
            print(f"Error: feature {feature_id} not found.", file=sys.stderr)
            sys.exit(1)

        pm = ProjectManager(store)
        project = await pm.get_project(feature.project_id)
        if project is None:
            print(f"Error: project {feature.project_id} not found.", file=sys.stderr)
            sys.exit(1)

        local_path = project.local_path
        intent_md_path = Path(local_path) / "docs" / "INTENT.md"
        if not intent_md_path.exists():
            print(f"Error: INTENT.md not found at {intent_md_path}", file=sys.stderr)
            sys.exit(1)

        intent_md = intent_md_path.read_text()

        print(f"Feature: {feature.title}")
        print(f"Project: {project.name} ({local_path})")

        specs = await fm.get_high_level_specs(feature_id)
        if not specs:
            print("No high-level specs found for this feature.")
            sys.exit(0)

        eligible = []
        for spec in specs:
            if await is_eligible(spec, fm, store):
                eligible.append(spec)

        print(f"\nTotal high-level specs: {len(specs)}")
        print(f"Eligible for compilation: {len(eligible)}")

        if not eligible:
            print("No specs are eligible for compilation.")
            sys.exit(0)

        compiled_count = 0
        failed_count = 0

        for hls in eligible:
            success = await compile_hls(
                hls=hls,
                feature_title=feature.title,
                project_id=feature.project_id,
                intent_md=intent_md,
                local_path=local_path,
                store=store,
                fm=fm,
                debug=debug,
            )
            if success:
                compiled_count += 1
            else:
                failed_count += 1

        print("\n--- Compilation Summary ---")
        print(f"Compiled: {compiled_count}")
        print(f"Failed:   {failed_count}")
        print(f"Skipped (not eligible): {len(specs) - len(eligible)}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
