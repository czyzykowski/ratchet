# Spec 10: Update CLAUDE.md

## Objective

Update CLAUDE.md to accurately reflect the current codebase state — adding missing modules from specs 06-09, documenting the worker, traces directory, venv usage, and flake.nix LD_LIBRARY_PATH fix.

## Success Criteria

- [ ] `core/execution_manager.py` appears in repo structure with correct description
- [ ] `core/context_assembler.py` appears in repo structure with correct description
- [ ] `core/invoker.py` appears in repo structure with correct description
- [ ] `worker/` directory appears in repo structure with description
- [ ] `worker/runner.py` described as: get_next_task, run_once, main — single-pass task executor
- [ ] Running Things section includes `python -m worker` with description
- [ ] Running Things section includes note: scripts must be run with `.venv/bin/python` not system python
- [ ] Key Patterns section includes: `ClaudeCodeInvoker` runs Claude Code as subprocess, captures output to XDG traces dir
- [ ] Key Patterns section includes: traces stored at `$XDG_DATA_HOME/ratchet/traces/` (default `~/.local/share/ratchet/traces/`)
- [ ] Key Patterns section includes: `flake.nix` shellHook sets `LD_LIBRARY_PATH` for libpq — required for psycopg
- [ ] Repo structure section includes `scripts/run-next.py` description update — clarified as thin wrapper over worker
- [ ] No content removed that is currently accurate
- [ ] `ruff check .` passes — CLAUDE.md is not Python, this just confirms no other files were accidentally modified
- [ ] `git diff --name-only` shows only `CLAUDE.md` was modified
- [ ] Commit: `git add -A && git commit -m "spec(10): update CLAUDE.md with specs 06-09 additions"`

## Out of Scope

- Do not modify any Python files
- Do not modify any other documentation files
- Do not modify migrations or tests
- Only modify `CLAUDE.md`

## Technical Context

- `CLAUDE.md` lives at repo root — this is where Claude Code reads it automatically
- Current CLAUDE.md is accurate for specs 01-05 and 09 — only additions needed, minimal changes
- Missing modules added in specs 06, 07, 08:
  - `core/context_assembler.py` — spec 06
  - `core/invoker.py` — spec 07
  - `core/execution_manager.py` — spec 04 (also missing)
  - `worker/` — spec 08

## Exact Additions Required

### Repo Structure — add to `core/` section:

```
  execution_manager.py — ExecutionManager, prepare/cleanup worktree environment
  context_assembler.py — ContextAssembler, ExecutionContext, assembles Claude Code prompt
  invoker.py           — ClaudeCodeInvoker, runs Claude Code subprocess, captures traces
```

### Repo Structure — add after `scripts/` section:

```
worker/
  __init__.py
  __main__.py     — enables python -m worker
  runner.py       — get_next_task, run_once, main — single-pass task executor
  tests/          — integration tests
```

### Key Patterns — add:

```
- `ContextAssembler` assembles execution prompt from INTENT.md, spec content, and knowledge placeholder
- `ClaudeCodeInvoker` runs `claude -p <prompt>` as subprocess in worktree, detects COMPLETED/BLOCKED markers
- Traces written to `$XDG_DATA_HOME/ratchet/traces/` (default `~/.local/share/ratchet/traces/`)
- `flake.nix` shellHook sets `LD_LIBRARY_PATH` for libpq — required for psycopg to find PostgreSQL client library
- All scripts must be run with `.venv/bin/python` — system Python does not have dependencies
```

### Running Things — add:

```bash
# Run worker single pass (pick and execute next ready task)
.venv/bin/python -m worker

# Run operational scripts (must use .venv/bin/python)
.venv/bin/python scripts/board.py
.venv/bin/python scripts/add-task.py --project-id <uuid> --title "My task"
```

### Update existing run-spec.sh note:

Change example from `specs/02-state-machine.md` to `specs/10-update-claude-md.md` to reflect current usage.

## Tasks

- [ ] Add `execution_manager.py`, `context_assembler.py`, `invoker.py` to repo structure core section
- [ ] Add `worker/` directory to repo structure section
- [ ] Add five new Key Patterns entries as specified above
- [ ] Add `.venv/bin/python -m worker` to Running Things section
- [ ] Add `.venv/bin/python` note to Running Things section
- [ ] Update run-spec.sh example to use a current spec filename
- [ ] Verify `git diff --name-only` shows only CLAUDE.md
- [ ] Run `ruff check .` to confirm no Python files accidentally modified
- [ ] Commit: `git add -A && git commit -m "spec(10): update CLAUDE.md with specs 06-09 additions"`

## Assumptions

- Postgres running on 127.0.0.1:5432, DATABASE_URL set
- All additions are additive — no existing accurate content should be removed
- Worker tests live in `worker/tests/` not `core/tests/`

## Verification Commands

```bash
ruff check .
git diff --name-only HEAD~1
```

## What Exists After This Spec

```
CLAUDE.md  — accurate and complete documentation of entire codebase
             any agent reading it has correct understanding of all modules,
             patterns, and operational procedures
```

CLAUDE.md is now the reliable entry point for any agent executing tasks in this repo.
