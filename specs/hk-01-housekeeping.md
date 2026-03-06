# Spec HK-01: Housekeeping — CLAUDE.md, Run Script, Commit Convention

## Objective

Relocate CLAUDE.md to the repo root, update it to reflect the current codebase state, add the execution script to the repo with improvements, and establish the commit-after-task convention.

## Success Criteria

- [ ] `CLAUDE.md` exists at repo root (`/home/lukasz/Code/ratchet/CLAUDE.md`)
- [ ] `docs/CLAUDE.md` no longer exists
- [ ] `CLAUDE.md` accurately describes current repo structure including `core/state_machine.py`, `core/spec_manager.py`, `core/tests/`
- [ ] `CLAUDE.md` documents the commit convention: `git add -A && git commit -m "spec(N): description"`
- [ ] `CLAUDE.md` documents that `InMemoryStore` is used in all unit tests — no database required
- [ ] `CLAUDE.md` documents that `PostgresStore` uses lazy import pattern to avoid psycopg import during test collection
- [ ] `CLAUDE.md` does NOT mention dual-write pattern — it was removed (or never existed in final form)
- [ ] `scripts/run-spec.sh` exists and is executable (`chmod +x`)
- [ ] Script reads verification commands from spec's `## Verification Commands` section rather than hardcoding them
- [ ] Script commits all changes after successful completion with message format `spec(N): <spec filename without extension>`
- [ ] Script warns if `.env` not present and falls back to `.env.example`
- [ ] Script blocker report format is preserved
- [ ] Script final report format includes commit hash
- [ ] `docs/INTENT.md` remains at `docs/INTENT.md` — do not move it
- [ ] All changes are committed with message `spec(hk-01): housekeeping CLAUDE.md and run script`

## Out of Scope

- Do not modify any files in `core/`
- Do not modify `db/` or migrations
- Do not modify `pyproject.toml` or `flake.nix`
- Do not update INTENT.md
- Do not add new functionality to the codebase

## Technical Context

- Working directory: `/home/lukasz/Code/ratchet`
- `CLAUDE.md` is read automatically by Claude Code when invoked in the repo root — it must be at root
- `docs/INTENT.md` is injected by the orchestrator at runtime — it stays in `docs/`
- Current codebase state to document in CLAUDE.md:
  - `core/events.py` — event type constants, task status constants
  - `core/models.py` — Pydantic models: Event, Project, Task, Spec, Execution
  - `core/store.py` — Store protocol, InMemoryStore, PostgresStore
  - `core/state_machine.py` — InvalidTransitionError, TaskStateMachine
  - `core/spec_manager.py` — SpecManager
  - `core/db.py` — async connection pool, reads DATABASE_URL
  - `core/tests/` — unit tests, run with `pytest core/tests/ -v`, no DB required
  - `db/migrations/` — Alembic migrations
  - `db/smoke_test.py` — integration smoke test, requires TEST_DATABASE_URL
  - `docs/INTENT.md` — project intent, injected by orchestrator at runtime
  - `scripts/run-spec.sh` — spec execution script

## CLAUDE.md Content to Write

```markdown
# Ratchet

## What This Is

Task orchestration system for autonomous AI-driven software development.
See `docs/INTENT.md` for project purpose and values.

## Repo Structure
```

core/
events.py — event type constants, task status constants
models.py — Pydantic models: Event, Project, Task, Spec, Execution
store.py — Store protocol, InMemoryStore, PostgresStore
state_machine.py — InvalidTransitionError, TaskStateMachine
spec_manager.py — SpecManager
db.py — async connection pool, reads DATABASE_URL from environment
tests/ — unit tests, no database required
db/
migrations/ — Alembic migrations (alembic upgrade head)
smoke_test.py — integration test, requires TEST_DATABASE_URL
docs/
INTENT.md — project intent statement (injected by orchestrator at runtime)
scripts/
run-spec.sh — execute a spec file via Claude Code
pyproject.toml — dependencies
flake.nix — reproducible dev shell (nix develop)
.env.example — connection string templates

````

## Database
- Postgres on 127.0.0.1:5432, database `ratchet`
- Application user: `ratchet` (used by alembic and PostgresStore)
- Test user: `ratchet_test` (used by smoke_test.py)
- Do NOT start or configure Postgres — it runs natively on NixOS
- Environment variables: `DATABASE_URL`, `TEST_DATABASE_URL`

## Key Patterns
- All core modules accept a `Store` instance — never import a concrete store directly
- `InMemoryStore` is used in all unit tests — fast, no database required
- `PostgresStore` uses lazy import of `core.db` to avoid psycopg import during test collection
- Events are append-only — never update or delete rows in the `events` table
- State is always derived from event replay — do not trust materialized views for correctness

## Running Things
```bash
# Enter dev shell
nix develop

# Run unit tests (no DB required)
pytest core/tests/ -v

# Run integration smoke test
python db/smoke_test.py

# Apply migrations
alembic upgrade head

# Execute a spec
scripts/run-spec.sh specs/02-state-machine.md
````

## Commit Convention

After completing all tasks in a spec, commit with:

```bash
git add -A && git commit -m "spec(N): description"
```

Example: `git commit -m "spec(03): spec entity and lineage chain"`
Every spec execution must end with a commit if all tasks succeeded.

````

## Improved `scripts/run-spec.sh` to Write
The script must:
1. Accept a single argument: path to spec file
2. Load `.env` if present, fall back to `.env.example` with a warning if not
3. Read the spec file in full and pass it as the system prompt via `claude -p`
4. Extract and run verification commands from the spec's `## Verification Commands` section
5. After all tasks and verifications pass, commit with `git add -A && git commit -m "spec(N): <basename>"`
6. Output the commit hash in the final report
7. Preserve the existing blocker report format
8. Write output to `<spec-dir>/<spec-basename>.output.md`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <spec-file>" >&2
  exit 1
fi

SPEC_FILE="$1"
if [[ ! -f "$SPEC_FILE" ]]; then
  echo "Error: file not found: $SPEC_FILE" >&2
  exit 1
fi

# Load environment
if [[ -f .env ]]; then
  set -a; source .env; set +a
elif [[ -f .env.example ]]; then
  echo "Warning: .env not found, using .env.example defaults" >&2
  set -a; source .env.example; set +a
else
  echo "Error: neither .env nor .env.example found" >&2
  exit 1
fi

DIR="$(dirname "$SPEC_FILE")"
BASENAME="$(basename "$SPEC_FILE" .md)"
OUTPUT_FILE="$DIR/$BASENAME.output.md"

PROMPT="You are executing the implementation spec at \`$SPEC_FILE\`.
Your job is to implement every task in the ## Tasks checklist, then verify every item
in the ## Success Criteria checklist.

## Instructions
1. Read \`$SPEC_FILE\` in full before starting.
2. Read \`CLAUDE.md\` at the repo root for codebase conventions.
3. Check what already exists — do not recreate files that are already correct.
4. Execute each task in the ## Tasks list in order. For each task:
   - Do the work (create/edit files, run commands)
   - If a task cannot be completed, STOP and output the blocker report below.
5. Run every command listed in the spec's ## Verification Commands section.
   Report PASS or FAIL with exact output for each.
6. If all tasks and verifications pass, commit:
   git add -A && git commit -m \"spec($BASENAME): all tasks complete\"
   Include the commit hash in the final report.

## Blocker Report Format
If you hit a blocker, output this and stop:
\`\`\`
BLOCKED: <task name>
Reason: <what went wrong>
Missing:
- <item 1>
User action required:
<exact steps the user must take to unblock>
Resume: re-run this script after fixing the above
\`\`\`

## Constraints
- Working directory: $(pwd)
- Do NOT start or configure Postgres
- Do NOT create Postgres users or databases
- DATABASE_URL and TEST_DATABASE_URL are already set in environment

## Final Report Format
\`\`\`
COMPLETED: $SPEC_FILE
Tasks completed: N/N
Verification:
  <command>: PASS
Files created/modified:
- <file>
Commit: <hash>
\`\`\`"

echo "Running: $SPEC_FILE -> $OUTPUT_FILE"
claude -p "$PROMPT" --allowedTools "Bash,Read,Write,Edit,Glob,Grep" | tee "$OUTPUT_FILE"
````

## Tasks

- [ ] Move `docs/CLAUDE.md` to repo root: `mv docs/CLAUDE.md CLAUDE.md`
- [ ] Rewrite `CLAUDE.md` with content specified above
- [ ] Create `scripts/` directory
- [ ] Write `scripts/run-spec.sh` with improved script as specified above
- [ ] Make script executable: `chmod +x scripts/run-spec.sh`
- [ ] Verify `docs/INTENT.md` still exists and is unchanged
- [ ] Run `scripts/run-spec.sh` with no arguments and confirm usage error is printed
- [ ] Commit all changes: `git add -A && git commit -m "spec(hk-01): housekeeping CLAUDE.md and run script"`

## Assumptions

- Git is initialized and has at least one prior commit
- `docs/CLAUDE.md` exists from spec 01
- `docs/INTENT.md` exists from spec 01 and should not be moved
- Working directory is `/home/lukasz/Code/ratchet`

## Verification Commands

```bash
test -f CLAUDE.md && echo "PASS: CLAUDE.md at root" || echo "FAIL: CLAUDE.md missing"
test ! -f docs/CLAUDE.md && echo "PASS: docs/CLAUDE.md removed" || echo "FAIL: docs/CLAUDE.md still exists"
test -x scripts/run-spec.sh && echo "PASS: script executable" || echo "FAIL: script not executable"
git log --oneline -1
```

## What Exists After This Spec

```
CLAUDE.md               — at repo root, current, read automatically by Claude Code
scripts/
  run-spec.sh           — improved execution script with auto-commit
docs/
  INTENT.md             — unchanged
```

Every future spec execution via `scripts/run-spec.sh` will automatically commit on success.
CLAUDE.md is now the authoritative guide for any agent working in this repo.
