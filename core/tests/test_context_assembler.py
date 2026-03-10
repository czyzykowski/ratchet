"""Unit tests for ContextAssembler using InMemoryStore — no database required."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core import events as ev
from core.context_assembler import (
    ContextAssembler,
    ContextAssemblyError,
    build_conflict_resolution_prompt,
    build_prompt,
    read_intent,
)
from core.store import InMemoryStore


def make_worktree(tmp_path: Path, intent_content: str = "# Test Intent") -> str:
    """Create minimal worktree structure with docs/INTENT.md."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "docs").mkdir()
    (worktree / "docs" / "INTENT.md").write_text(intent_content)
    return str(worktree)


async def _seed_store(
    store: InMemoryStore,
    worktree_path: str,
    spec_content: str = "# My Spec",
    status: str = "running",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed store with EXECUTION_STARTED and SPEC_CREATED events.

    Returns (execution_id, task_id, spec_id).
    """
    execution_id = uuid.uuid4()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "execution_id": str(execution_id),
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "worktree_path": worktree_path,
            "status": "running",
        },
    )

    # If status is not running, append terminal event
    if status == "completed":
        await store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_COMPLETED,
            payload={"execution_id": str(execution_id), "status": "completed"},
        )
    elif status == "failed":
        await store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_FAILED,
            payload={
                "execution_id": str(execution_id),
                "status": "failed",
                "failure_reason": "something went wrong",
            },
        )

    if spec_content is not None:
        await store.append_event(
            aggregate_id=spec_id,
            aggregate_type="spec",
            event_type=ev.SPEC_CREATED,
            payload={
                "spec_id": str(spec_id),
                "task_id": str(task_id),
                "content": spec_content,
                "previous_spec_id": None,
            },
        )

    return execution_id, task_id, spec_id


# ---------------------------------------------------------------------------
# Successful assembly
# ---------------------------------------------------------------------------


async def test_assemble_returns_execution_context_with_correct_ids(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, task_id, spec_id = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert ctx.execution_id == execution_id
    assert ctx.task_id == task_id
    assert ctx.spec_id == spec_id
    assert ctx.worktree_path == worktree


async def test_assemble_prompt_contains_intent_content(tmp_path: Path) -> None:
    store = InMemoryStore()
    intent = "# Project Intent\nThis is the intent."
    worktree = make_worktree(tmp_path, intent_content=intent)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert intent in ctx.prompt


async def test_assemble_prompt_contains_spec_content(tmp_path: Path) -> None:
    store = InMemoryStore()
    spec = "# Spec\n## Tasks\n- [ ] Do the thing"
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, spec_content=spec)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert spec in ctx.prompt


async def test_assemble_prompt_contains_knowledge_placeholder(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert "(no relevant knowledge entries for this execution)" in ctx.prompt


# ---------------------------------------------------------------------------
# Prompt section ordering
# ---------------------------------------------------------------------------


async def test_assemble_prompt_section_order(tmp_path: Path) -> None:
    store = InMemoryStore()
    intent = "# Intent Content"
    spec = "# Spec Content"
    worktree = make_worktree(tmp_path, intent_content=intent)
    execution_id, _, _ = await _seed_store(store, worktree, spec_content=spec)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    preamble_pos = ctx.prompt.index("## Instructions")
    intent_pos = ctx.prompt.index("## Project Intent")
    knowledge_pos = ctx.prompt.index("## Knowledge")
    spec_pos = ctx.prompt.index("## Spec")

    assert preamble_pos < intent_pos < knowledge_pos < spec_pos


async def test_assemble_prompt_preamble_text_present(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert "You are executing a software development task autonomously." in ctx.prompt
    assert "Read `CLAUDE.md`" in ctx.prompt


# ---------------------------------------------------------------------------
# ContextAssemblyError — execution not found
# ---------------------------------------------------------------------------


async def test_assemble_raises_when_execution_not_found() -> None:
    store = InMemoryStore()
    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="not found"):
        await assembler.assemble(uuid.uuid4())


# ---------------------------------------------------------------------------
# ContextAssemblyError — execution status not running
# ---------------------------------------------------------------------------


async def test_assemble_raises_when_execution_completed(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, status="completed")

    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="not running"):
        await assembler.assemble(execution_id)


async def test_assemble_raises_when_execution_failed(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, status="failed")

    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="not running"):
        await assembler.assemble(execution_id)


# ---------------------------------------------------------------------------
# ContextAssemblyError — INTENT.md missing
# ---------------------------------------------------------------------------


async def test_assemble_raises_when_intent_missing(tmp_path: Path) -> None:
    store = InMemoryStore()
    # Create worktree without docs/INTENT.md
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "docs").mkdir()
    execution_id, _, _ = await _seed_store(store, str(worktree))

    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="INTENT.md"):
        await assembler.assemble(execution_id)


# ---------------------------------------------------------------------------
# ContextAssemblyError — spec content empty or None
# ---------------------------------------------------------------------------


async def test_assemble_raises_when_spec_content_empty(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, spec_content="")

    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="empty or missing"):
        await assembler.assemble(execution_id)


async def test_assemble_raises_when_no_spec_event(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, spec_content=None)

    assembler = ContextAssembler(store)

    with pytest.raises(ContextAssemblyError, match="empty or missing"):
        await assembler.assemble(execution_id)


# ---------------------------------------------------------------------------
# Prompt content fidelity
# ---------------------------------------------------------------------------


async def test_multiline_intent_preserved(tmp_path: Path) -> None:
    store = InMemoryStore()
    intent = "# Intent\n\nLine one.\nLine two.\n\n## Section\nContent here."
    worktree = make_worktree(tmp_path, intent_content=intent)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert intent in ctx.prompt


async def test_markdown_spec_preserved(tmp_path: Path) -> None:
    store = InMemoryStore()
    spec = "# Spec\n\n## Tasks\n\n- [ ] Task one\n- [ ] Task two\n\n```python\nprint('hello')\n```"
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree, spec_content=spec)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert spec in ctx.prompt


# ---------------------------------------------------------------------------
# Module-level function tests
# ---------------------------------------------------------------------------


def test_read_intent_returns_file_contents(tmp_path: Path) -> None:
    worktree = make_worktree(tmp_path, intent_content="# My Intent\nSome content.")
    result = read_intent(worktree)
    assert result == "# My Intent\nSome content."


def test_read_intent_raises_when_missing(tmp_path: Path) -> None:
    worktree = tmp_path / "empty"
    worktree.mkdir()
    with pytest.raises(ContextAssemblyError, match="INTENT.md"):
        read_intent(str(worktree))


def test_build_prompt_contains_all_sections() -> None:
    prompt = build_prompt("intent text", "spec text")
    assert "## Instructions" in prompt
    assert "## Project Intent" in prompt
    assert "intent text" in prompt
    assert "## Knowledge" in prompt
    assert "(no relevant knowledge entries for this execution)" in prompt
    assert "## Spec" in prompt
    assert "spec text" in prompt


def test_build_prompt_section_order() -> None:
    prompt = build_prompt("INTENT_CONTENT", "SPEC_CONTENT")
    assert prompt.index("## Instructions") < prompt.index("## Project Intent")
    assert prompt.index("## Project Intent") < prompt.index("## Knowledge")
    assert prompt.index("## Knowledge") < prompt.index("## Spec")


def test_build_prompt_contains_completion_instructions() -> None:
    prompt = build_prompt("intent text", "spec text")
    assert "COMPLETED:" in prompt
    assert "BLOCKED:" in prompt
    assert "## Completion Instructions" in prompt


async def test_assemble_prompt_contains_completion_instructions(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert "COMPLETED:" in ctx.prompt
    assert "BLOCKED:" in ctx.prompt


# ---------------------------------------------------------------------------
# QA feedback — build_prompt unit tests
# ---------------------------------------------------------------------------


def test_build_prompt_includes_qa_feedback_section_when_provided() -> None:
    feedback = "Tests failed: missing import in module X"
    prompt = build_prompt("intent", "spec", qa_feedback=feedback)
    assert "## Previous Attempt Feedback" in prompt
    assert feedback in prompt


def test_build_prompt_excludes_qa_feedback_section_when_none() -> None:
    prompt = build_prompt("intent", "spec", qa_feedback=None)
    assert "## Previous Attempt Feedback" not in prompt


def test_build_prompt_qa_feedback_section_order() -> None:
    feedback = "QA_FAILURE_TEXT"
    prompt = build_prompt("INTENT_TEXT", "SPEC_TEXT", qa_feedback=feedback)
    spec_pos = prompt.index("## Spec")
    feedback_pos = prompt.index("## Previous Attempt Feedback")
    completion_pos = prompt.index("## Completion Instructions")
    assert spec_pos < feedback_pos < completion_pos


# ---------------------------------------------------------------------------
# QA feedback — ContextAssembler integration tests
# ---------------------------------------------------------------------------


async def test_assemble_includes_qa_feedback_when_task_was_blocked(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, task_id, _ = await _seed_store(store, worktree)

    failure_reason = "QA detected missing implementation of feature X"
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={
            "task_id": str(task_id),
            "to_status": "blocked",
            "failure_reason": failure_reason,
        },
    )

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert "## Previous Attempt Feedback" in ctx.prompt
    assert failure_reason in ctx.prompt


async def test_assemble_excludes_qa_feedback_when_task_never_blocked(tmp_path: Path) -> None:
    store = InMemoryStore()
    worktree = make_worktree(tmp_path)
    execution_id, _, _ = await _seed_store(store, worktree)

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id)

    assert "## Previous Attempt Feedback" not in ctx.prompt


# ---------------------------------------------------------------------------
# build_conflict_resolution_prompt tests
# ---------------------------------------------------------------------------


def test_build_conflict_resolution_prompt_contains_all_four_sections() -> None:
    prompt = build_conflict_resolution_prompt(
        intent_content="# Intent\nProject intent here.",
        spec_content="# Spec\nSpec content here.",
        conflicted_files=["src/foo.py", "src/bar.py"],
        merge_output="CONFLICT (content): Merge conflict in src/foo.py",
    )
    assert "## Project Intent" in prompt
    assert "## Original Spec" in prompt
    assert "## Conflict Details" in prompt
    assert "## Conflict Resolution Instructions" in prompt


def test_build_conflict_resolution_prompt_contains_conflicted_filenames() -> None:
    prompt = build_conflict_resolution_prompt(
        intent_content="intent",
        spec_content="spec",
        conflicted_files=["src/foo.py", "src/bar.py"],
        merge_output="some merge output",
    )
    assert "src/foo.py" in prompt
    assert "src/bar.py" in prompt


def test_build_conflict_resolution_prompt_contains_merge_output() -> None:
    merge_output = "CONFLICT (content): Merge conflict in src/foo.py"
    prompt = build_conflict_resolution_prompt(
        intent_content="intent",
        spec_content="spec",
        conflicted_files=["src/foo.py"],
        merge_output=merge_output,
    )
    assert merge_output in prompt


def test_build_conflict_resolution_prompt_does_not_contain_commit_instruction() -> None:
    prompt = build_conflict_resolution_prompt(
        intent_content="intent",
        spec_content="spec",
        conflicted_files=["src/foo.py"],
        merge_output="merge output",
    )
    assert "git add -A && git commit" not in prompt
    assert "Before declaring COMPLETED you MUST commit" not in prompt


def test_build_conflict_resolution_prompt_stage_only_instruction_present() -> None:
    prompt = build_conflict_resolution_prompt(
        intent_content="intent",
        spec_content="spec",
        conflicted_files=["src/foo.py"],
        merge_output="merge output",
    )
    assert "Do NOT commit" in prompt
    assert "git add" in prompt
