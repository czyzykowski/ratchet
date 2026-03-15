"""Execution context assembler: gathers all information needed for a Claude Code execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from core import events as ev
from core import qa_manager
from core.models import Project, QAExchange
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

_QA_HISTORY_HEADER = """\
## Human Input History

The following questions were asked during previous execution attempts,
along with the human's answers. Use these answers to guide your
implementation decisions."""

_COMPLETION_INSTRUCTIONS = """\
---

## Completion Instructions

**CRITICAL: You MUST commit AND then print the exact terminal marker as your very last output.**
The worker scans your stdout for a literal string. If that string is absent the execution
is recorded as failed — even if your code is correct and committed.

**Do not paraphrase.** Phrases like "All done", "The task is complete", "Everything looks good",
or "232 tests passed" are NOT detected. You must print the exact word `COMPLETED` (or `BLOCKED:`)
as the final thing you output.

### On success

First commit:
```bash
git add -A && git commit -m "feat: <brief description of change>"
```

Then verify the commit exists:
```bash
git log --oneline -3
```

Then print this as your last output:
```
COMPLETED: <spec title or brief description>
Tasks completed: N/N
Files created/modified:
- <file 1>
- <file 2>
```

### On failure

Emit `BLOCKED: <reason>` (without committing) when:
- A required external resource is unavailable (missing env var, unreachable service).
- A prerequisite task is incomplete and this spec cannot proceed without it.
- You have exhausted all fix attempts and cannot resolve QA failures.

Do NOT emit BLOCKED for normal compilation errors, test failures, or missing files you
can create — fix those and proceed. BLOCKED is a last resort that signals a human must
intervene before the task can continue. The reason is stored as the task's failure message.

```
BLOCKED: <task name that failed>
Reason: <what went wrong and what would unblock it>
Missing:
- <item 1>
User action required:
<exact steps to unblock>
Resume: re-run after fixing the above
```"""


@dataclass
class ExecutionContext:
    execution_id: UUID
    task_id: UUID
    spec_id: UUID
    worktree_path: str
    prompt: str  # fully assembled prompt string, ready to pass to Claude Code


class ContextAssemblyError(Exception):
    """Raised when execution context cannot be assembled. Message states reason."""


def read_intent(worktree_path: str, intent_md: str | None = None) -> str:
    """Read docs/INTENT.md from worktree, or return intent_md override directly.

    When intent_md is not None, returns it immediately without touching disk.
    Raises ContextAssemblyError if file not found (disk path only).
    Returns file contents as string.
    """
    if intent_md is not None:
        return intent_md
    intent_path = Path(worktree_path) / "docs" / "INTENT.md"
    if not intent_path.exists():
        raise ContextAssemblyError(
            f"INTENT.md not found at {intent_path}"
        )
    return intent_path.read_text()


def build_qa_history_section(qa_history: list[QAExchange]) -> str | None:
    """Build the ## Human Input History markdown section from answered exchanges.

    Returns None if qa_history is empty or all answers are None.
    Returns formatted markdown section string otherwise.
    """
    answered = [x for x in qa_history if x.answer is not None]
    if not answered:
        return None
    parts = [_QA_HISTORY_HEADER]
    for exchange in answered:
        n = exchange.question_index + 1
        parts.append(
            f"### Question {n}\n\n{exchange.question}\n\n**Answer:** {exchange.answer}"
        )
    return "\n\n".join(parts)


def build_prompt(
    intent_content: str,
    spec_content: str,
    qa_feedback: str | None = None,
    qa_history: list[QAExchange] | None = None,
) -> str:
    """Assemble final prompt string from components.

    Follows section order: preamble → intent → knowledge placeholder → spec
    → (optional) human input history → (optional) previous attempt feedback
    → completion instructions.
    Returns complete prompt string.
    """
    parts = [
        _PREAMBLE,
        f"## Project Intent\n{intent_content}",
        "---",
        _KNOWLEDGE_PLACEHOLDER,
        f"## Spec\n{spec_content}",
    ]
    if qa_history is not None:
        section = build_qa_history_section(qa_history)
        if section is not None:
            parts.append(section)
    if qa_feedback is not None:
        parts.append(f"## Previous Attempt Feedback\n{qa_feedback}")
    parts.append(_COMPLETION_INSTRUCTIONS)
    return "\n\n".join(parts)


_CONFLICT_RESOLUTION_INSTRUCTIONS = """\
---

## Conflict Resolution Instructions

Resolve all merge conflicts listed above. For each file:
1. Open the file and resolve all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Run `git add <file>` to stage the resolved file
3. Do NOT commit — the deployment script will commit after all conflicts are resolved

When all conflicted files are staged, output:
```
COMPLETED: all conflicts resolved and staged
```

If you cannot resolve a conflict in any file, output:
```
BLOCKED: <filename>
Reason: <explanation of why the conflict cannot be resolved automatically>
```"""


def build_conflict_resolution_prompt(
    intent_content: str,
    spec_content: str,
    conflicted_files: list[str],
    merge_output: str,
) -> str:
    """Assemble conflict resolution prompt for Claude Code.

    Sections: project intent → original spec → conflict details → resolution instructions.
    Does NOT include standard commit-before-COMPLETED instructions.
    Returns complete prompt string.
    """
    file_list = "\n".join(f"- {f}" for f in conflicted_files)
    parts = [
        f"## Project Intent\n{intent_content}",
        "---",
        f"## Original Spec\n{spec_content}",
        "---",
        (
            f"## Conflict Details\n\n### Conflicted Files\n{file_list}"
            f"\n\n### Merge Output\n```\n{merge_output}\n```"
        ),
        _CONFLICT_RESOLUTION_INSTRUCTIONS,
    ]
    return "\n\n".join(parts)


class ContextAssembler:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def assemble(
        self, execution_id: UUID, project: Project | None = None
    ) -> ExecutionContext:
        """Assemble complete execution context for a running execution.

        Steps:
        1. Replay events to find Execution with matching execution_id
           — raise ContextAssemblyError if not found
           — raise ContextAssemblyError if status != 'running'
        2. Replay events to find Spec for execution's spec_id
           — raise ContextAssemblyError if spec content is empty or None
        3. Call read_intent(execution.worktree_path, intent_md)
           — intent_md is project.intent_md when project.config_source == "db"
           — raises ContextAssemblyError if INTENT.md not found (disk path only)
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
        intent_md_override: str | None = None
        if project is not None and project.config_source == "db":
            intent_md_override = project.intent_md
        intent_content = read_intent(worktree_path, intent_md_override)

        # Step 3b: find most recent BLOCKED event with failure_reason
        task_events = await self._store.get_events(task_id, "task")
        qa_feedback: str | None = None
        for event in task_events:
            if (
                event.event_type == ev.TASK_STATUS_CHANGED
                and event.payload.get("to_status") == "blocked"
                and event.payload.get("failure_reason")
            ):
                qa_feedback = event.payload["failure_reason"]

        # Step 3c: get Q&A history for this task
        qa_history = await qa_manager.get_qa_history(self._store, task_id)

        # Step 4: build prompt
        prompt = build_prompt(
            intent_content,
            spec_content,
            qa_feedback=qa_feedback,
            qa_history=qa_history,
        )

        # Step 5: return context
        return ExecutionContext(
            execution_id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            worktree_path=worktree_path,
            prompt=prompt,
        )
