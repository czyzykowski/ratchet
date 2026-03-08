"""Unit tests for ContextAssembler using InMemoryStore — no database required."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core import events as ev
from core.context_assembler import ContextAssembler, ContextAssemblyError, build_prompt, read_intent
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
