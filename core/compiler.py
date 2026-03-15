"""HLS compilation logic: extract, evaluate eligibility, and compile high-level specs into tasks."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from core.models_config import CHAT_MODEL

if TYPE_CHECKING:
    from core.feature_manager import FeatureManager
    from core.models import HighLevelSpec
    from core.store import Store

logger = logging.getLogger(__name__)


def run_claude(prompt: str, local_path: str, debug: bool = False) -> str:
    """Run claude -p non-interactively, stream output, and return full output."""
    if debug:
        print("\n--- DEBUG: PROMPT SENT TO CLAUDE ---", file=sys.stderr)
        print(prompt, file=sys.stderr)
        print("--- END PROMPT ---\n", file=sys.stderr)

    cmd = ["claude", "-p", prompt, "--model", CHAT_MODEL, "--allowedTools", "Read,Glob,Bash"]
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
      and that task's status is merged
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
    import shutil

    from core import events as ev
    from core.spec_manager import SpecManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine

    if not shutil.which("claude"):
        logger.error("claude binary not found in PATH; cannot compile HLS %s", hls.id)
        return False

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

    # Propagate HLS dependencies to task dependencies
    if hls.dependencies:
        # Resolve HLS IDs to task IDs
        all_specs = await fm.get_high_level_specs(hls.feature_id)
        specs_by_id = {s.id: s for s in all_specs}
        dep_task_ids = []
        for dep_hls_id in hls.dependencies:
            dep_spec = specs_by_id.get(dep_hls_id)
            if dep_spec is not None and dep_spec.task_id is not None:
                dep_task_ids.append(str(dep_spec.task_id))
        if dep_task_ids:
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPENDENCY_ADDED,
                payload={"depends_on": dep_task_ids},
            )
            print(f"  Depends on tasks: {', '.join(dep_task_ids)}")

    # Mark the high-level spec as compiled
    await fm.mark_compiled(hls.id, task_id, hls.feature_id)

    print(f"  Created task: {task_id}")
    print(f"  Task status: {ev.READY_FOR_IMPLEMENTATION}")
    return True


async def compile_all(store: Store) -> int:
    """Compile all eligible HLS entries across all active projects and features.

    Loads all active projects via ProjectManager, their features via FeatureManager,
    their HLS via FeatureManager.get_high_level_specs(), and calls is_eligible() for each.
    Calls compile_hls() for eligible ones.

    Returns count of successfully compiled specs.
    """
    from core.feature_manager import FeatureManager
    from core.project_manager import ProjectManager

    pm = ProjectManager(store)
    fm = FeatureManager(store)

    projects = await pm.list_projects()
    compiled_count = 0

    for project in projects:
        local_path = project.local_path
        if project.config_source == "db":
            if not project.intent_md:
                logger.warning(
                    "intent_md not set in DB for project %s; skipping", project.name
                )
                continue
            intent_md = project.intent_md
        else:
            intent_md_path = Path(local_path) / "docs" / "INTENT.md"
            if not intent_md_path.exists():
                logger.warning(
                    "INTENT.md not found at %s; skipping project %s", intent_md_path, project.name
                )
                continue
            intent_md = intent_md_path.read_text()
        features = await fm.list_features(project.id)

        for feature in features:
            specs = await fm.get_high_level_specs(feature.id)
            for hls in specs:
                if await is_eligible(hls, fm, store):
                    success = await compile_hls(
                        hls=hls,
                        feature_title=feature.title,
                        project_id=project.id,
                        intent_md=intent_md,
                        local_path=local_path,
                        store=store,
                        fm=fm,
                    )
                    if success:
                        compiled_count += 1

    return compiled_count
