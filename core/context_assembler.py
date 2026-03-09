"""Execution context assembler: gathers all information needed for a Claude Code execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from core import events as ev
from core.store import Store

_PREAMBLE = """\
## Instructions
You are executing a software development task autonomously.

Before starting:
1. Read `CLAUDE.md` at the repo root — it contains project conventions and commands.
2. Follow the spec below exactly.
3. Verify every item in the Success Criteria before reporting completion.

---"""

_KNOWLEDGE_PLACEHOLDER = """\
## Knowledge
(no relevant knowledge entries for this execution)

---"""

_COMPLETION_INSTRUCTIONS = """\
---

## Completion Instructions

**Before declaring COMPLETED you MUST commit your changes.**
The QA system verifies your work by inspecting the git diff on your branch.
Without a commit your changes are lost and QA will always fail.

Commit step (required):
```bash
git add -A && git commit -m "feat: <brief description of change>"
```

Only after committing, output one of these markers:

On success:
```
COMPLETED: <spec title or brief description>
Tasks completed: N/N
Files created/modified:
- <file 1>
- <file 2>
```

On failure or if you cannot complete the task:
```
BLOCKED: <task name that failed>
Reason: <what went wrong>
Missing:
- <item 1>
User action required:
<exact steps to unblock>
Resume: re-run after fixing the above
```

These markers are parsed by the orchestrator. Without them the execution will be
marked as failed."""


@dataclass
class ExecutionContext:
    execution_id: UUID
    task_id: UUID
    spec_id: UUID
    worktree_path: str
    prompt: str  # fully assembled prompt string, ready to pass to Claude Code


class ContextAssemblyError(Exception):
    """Raised when execution context cannot be assembled. Message states reason."""


def read_intent(worktree_path: str) -> str:
    """Read docs/INTENT.md from worktree.

    Raises ContextAssemblyError if file not found.
    Returns file contents as string.
    """
    intent_path = Path(worktree_path) / "docs" / "INTENT.md"
    if not intent_path.exists():
        raise ContextAssemblyError(
            f"INTENT.md not found at {intent_path}"
        )
    return intent_path.read_text()


def build_prompt(intent_content: str, spec_content: str) -> str:
    """Assemble final prompt string from components.

    Follows section order: preamble → intent → knowledge placeholder → spec.
    Returns complete prompt string.
    """
    return "\n\n".join([
        _PREAMBLE,
        f"## Project Intent\n{intent_content}",
        "---",
        _KNOWLEDGE_PLACEHOLDER,
        f"## Spec\n{spec_content}",
        _COMPLETION_INSTRUCTIONS,
    ])


class ContextAssembler:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def assemble(self, execution_id: UUID) -> ExecutionContext:
        """Assemble complete execution context for a running execution.

        Steps:
        1. Replay events to find Execution with matching execution_id
           — raise ContextAssemblyError if not found
           — raise ContextAssemblyError if status != 'running'
        2. Replay events to find Spec for execution's spec_id
           — raise ContextAssemblyError if spec content is empty or None
        3. Call read_intent(execution.worktree_path)
           — raises ContextAssemblyError if INTENT.md not found
        4. Call build_prompt(intent_content, spec.content)
        5. Return ExecutionContext
        """
        # Step 1: find and validate execution
        execution_events = await self._store.get_events(execution_id, "execution")
        started_event = None
        final_event = None
        for event in execution_events:
            if event.event_type == ev.EXECUTION_STARTED:
                started_event = event
            elif event.event_type in (ev.EXECUTION_COMPLETED, ev.EXECUTION_FAILED):
                final_event = event

        if started_event is None:
            raise ContextAssemblyError(f"Execution {execution_id} not found")

        status = "running" if final_event is None else final_event.payload["status"]
        if status != "running":
            raise ContextAssemblyError(
                f"Execution {execution_id} is not running (status={status!r})"
            )

        p = started_event.payload
        task_id = UUID(p["task_id"])
        spec_id = UUID(p["spec_id"])
        worktree_path: str = p["worktree_path"]

        # Step 2: find and validate spec
        spec_events = await self._store.get_events(spec_id, "spec")
        spec_content: str | None = None
        for event in spec_events:
            if event.event_type == ev.SPEC_CREATED:
                spec_content = event.payload.get("content")
                break

        if not spec_content:
            raise ContextAssemblyError(
                f"Spec {spec_id} has empty or missing content"
            )

        # Step 3: read INTENT.md (may raise ContextAssemblyError)
        intent_content = read_intent(worktree_path)

        # Step 4: build prompt
        prompt = build_prompt(intent_content, spec_content)

        # Step 5: return context
        return ExecutionContext(
            execution_id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            worktree_path=worktree_path,
            prompt=prompt,
        )
