# Spec 06: Execution Context Assembly

## Objective

Implement the execution context assembler — the module that gathers all information needed for a Claude Code execution and produces a structured, self-contained prompt package ready to be passed to the invoker.

## Success Criteria

- [ ] `core/context_assembler.py` implements `ContextAssembler` class accepting a `Store` instance
- [ ] `ContextAssembler.assemble(execution_id)` returns an `ExecutionContext` dataclass
- [ ] `ExecutionContext` contains: `prompt`, `worktree_path`, `execution_id`, `task_id`, `spec_id`
- [ ] `prompt` is a single string combining preamble, INTENT.md content, and spec content in correct order
- [ ] `assemble()` raises `ContextAssemblyError` if execution not found
- [ ] `assemble()` raises `ContextAssemblyError` if execution is not in `running` status
- [ ] `assemble()` raises `ContextAssemblyError` if INTENT.md not found at `<worktree_path>/docs/INTENT.md`
- [ ] `assemble()` raises `ContextAssemblyError` if spec content is empty or None
- [ ] `read_intent(worktree_path)` is implemented as a module-level function
- [ ] `build_prompt(intent_content, spec_content)` is implemented as a module-level function
- [ ] Knowledge entries section is present in prompt as a clearly marked placeholder — empty for now
- [ ] Unit tests in `core/tests/test_context_assembler.py` use `InMemoryStore` and `tmp_path` for worktree simulation
- [ ] Unit tests cover successful assembly — prompt contains intent content and spec content
- [ ] Unit tests cover prompt section ordering — preamble first, then intent, then knowledge placeholder, then spec
- [ ] Unit tests cover `ContextAssemblyError` when execution not found
- [ ] Unit tests cover `ContextAssemblyError` when execution status is not `running`
- [ ] Unit tests cover `ContextAssemblyError` when INTENT.md missing from worktree
- [ ] Unit tests cover `ContextAssemblyError` when spec content is empty
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ -v` passes with no errors and no database connection required
- [ ] Commit: `git add -A && git commit -m "spec(06): execution context assembly"`

## Out of Scope

- Do not invoke Claude Code — that is spec 07
- Do not implement knowledge retrieval or RAG — knowledge section is a placeholder
- Do not modify database schema or migrations
- Do not implement TUI, API, or worker
- Do not modify any existing core modules except to import from them
- Only create `core/context_assembler.py` and `core/tests/test_context_assembler.py`

## Technical Context

- Language: Python 3.12
- Pattern: same as other managers — accepts `Store` instance, never imports concrete store
- `read_intent()` uses `pathlib.Path` — reads file from worktree, no subprocess
- Existing files:
  - `core/store.py` — Store protocol, InMemoryStore
  - `core/models.py` — Execution, Spec, Task Pydantic models
  - `core/events.py` — event type constants
  - `core/execution_manager.py` — reference for how execution and worktree_path relate
  - `core/spec_manager.py` — reference for how spec content is stored

## ExecutionContext Dataclass

```python
@dataclass
class ExecutionContext:
    execution_id: UUID
    task_id: UUID
    spec_id: UUID
    worktree_path: str
    prompt: str             # fully assembled prompt string, ready to pass to Claude Code
```

## Prompt Structure

The assembled prompt must follow this exact section order:

```
## Instructions
You are executing a software development task autonomously.

Before starting:
1. Read `CLAUDE.md` at the repo root — it contains project conventions and commands.
2. Follow the spec below exactly.
3. Verify every item in the Success Criteria before reporting completion.

---

## Project Intent
<contents of docs/INTENT.md>

---

## Knowledge
(no relevant knowledge entries for this execution)

---

## Spec
<full spec content>
```

## Module-Level Functions

```python
def read_intent(worktree_path: str) -> str:
    """
    Read docs/INTENT.md from worktree.
    Raises ContextAssemblyError if file not found.
    Returns file contents as string.
    """

def build_prompt(intent_content: str, spec_content: str) -> str:
    """
    Assemble final prompt string from components.
    Follows section order defined above.
    Returns complete prompt string.
    """
```

## ContextAssembler Interface

```python
class ContextAssemblyError(Exception):
    """Raised when execution context cannot be assembled. Message states reason."""

class ContextAssembler:
    def __init__(self, store: Store) -> None: ...

    async def assemble(self, execution_id: UUID) -> ExecutionContext:
        """
        Assemble complete execution context for a running execution.

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
```

## Test Setup Pattern

```python
def make_worktree(tmp_path: Path, intent_content: str = "# Test Intent") -> str:
    """Create minimal worktree structure with docs/INTENT.md."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "docs").mkdir()
    (worktree / "docs" / "INTENT.md").write_text(intent_content)
    return str(worktree)
```

To populate InMemoryStore for tests, use events directly:

- Append `EXECUTION_STARTED` event with execution_id, task_id, spec_id, worktree_path, status="running"
- Append `SPEC_CREATED` event with spec_id, task_id, content

## Test Scenarios to Cover

```
Successful assembly:
- assemble() returns ExecutionContext with correct execution_id, task_id, spec_id, worktree_path
- prompt contains intent content from INTENT.md
- prompt contains spec content
- prompt sections appear in correct order: preamble → intent → knowledge placeholder → spec
- knowledge placeholder text is present in prompt

Error cases:
- execution_id not found → ContextAssemblyError
- execution status is 'completed' → ContextAssemblyError
- execution status is 'failed' → ContextAssemblyError
- INTENT.md missing from worktree → ContextAssemblyError
- spec content is empty string → ContextAssemblyError
- spec content is None → ContextAssemblyError

Prompt content:
- INTENT.md content with multiple lines is preserved correctly
- spec content with markdown formatting is preserved correctly
```

## Tasks

- [ ] Create `core/context_assembler.py` with `ContextAssemblyError`, `ExecutionContext` dataclass, module-level `read_intent()` and `build_prompt()`, and `ContextAssembler` class
- [ ] Implement `read_intent(worktree_path)` — read `<worktree_path>/docs/INTENT.md`, raise `ContextAssemblyError` if missing
- [ ] Implement `build_prompt(intent_content, spec_content)` — assemble prompt string following exact section order above
- [ ] Implement `assemble()` — find execution, validate status, find spec, read intent, build prompt, return ExecutionContext
- [ ] Create `core/tests/test_context_assembler.py` with `make_worktree` helper and all scenarios above
- [ ] Run `pytest core/tests/ -v` and confirm all tests pass with no database connection
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(06): execution context assembly"`

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- Worktree always contains `docs/INTENT.md` if created from a valid onboarded repo — missing file is an error worth surfacing
- Knowledge section is intentionally empty in v1 — placeholder text is sufficient
- `InMemoryStore` events are populated directly in tests — no need to use manager classes
- spec content is stored as plain text/markdown in the SPEC_CREATED event payload

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
```

## What Exists After This Spec

```
core/
  context_assembler.py  — ContextAssemblyError, ExecutionContext, read_intent,
                          build_prompt, ContextAssembler
  tests/
    test_state_machine.py      — unchanged
    test_spec_manager.py       — unchanged
    test_execution_manager.py  — unchanged
    test_project_manager.py    — unchanged
    test_context_assembler.py  — full unit test suite, no DB required
```

Execution context assembly is fully implemented and tested. The prompt structure is defined and stable. All components are assembled from event store — no filesystem access except INTENT.md read from worktree. Ready for Claude Code invocation in spec 07.
