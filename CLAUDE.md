# Ratchet

## What This Is

Task orchestration system for autonomous AI-driven software development.
See `docs/INTENT.md` for project purpose and values.

## Repo Structure

```
core/
  events.py        — event type constants, task status constants
  models.py        — Pydantic models: Event, Project, Task, Spec, Execution
  store.py         — Store protocol, InMemoryStore, PostgresStore
  state_machine.py — InvalidTransitionError, TaskStateMachine
  spec_manager.py  — SpecManager
  db.py            — async connection pool, reads DATABASE_URL from environment
  tests/           — unit tests, no database required
db/
  migrations/      — Alembic migrations (alembic upgrade head)
  smoke_test.py    — integration test, requires TEST_DATABASE_URL
docs/
  INTENT.md        — project intent statement (injected by orchestrator at runtime)
scripts/
  run-spec.sh      — execute a spec file via Claude Code
pyproject.toml     — dependencies
flake.nix          — reproducible dev shell (nix develop)
.env.example       — connection string templates
```

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
```

## Commit Convention

After completing all tasks in a spec, commit with:

```bash
git add -A && git commit -m "spec(N): description"
```

Example: `git commit -m "spec(03): spec entity and lineage chain"`
Every spec execution must end with a commit if all tasks succeeded.
