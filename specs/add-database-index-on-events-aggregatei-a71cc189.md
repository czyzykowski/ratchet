# Spec 15: Add database indexes on events aggregate_id and aggregate_type

## Objective
Add two single-column indexes to the `events` table — one on `aggregate_id` and one on `aggregate_type` — via a new Alembic migration. These complement the existing composite index `events_aggregate_idx (aggregate_id, aggregate_type, sequence)` by supporting queries that filter on either column independently.

## Success Criteria
- [ ] A new Alembic migration file exists in `db/migrations/versions/` with `down_revision` pointing to `a3f8b2e1d9c0`
- [ ] `upgrade()` creates index `events_aggregate_id_idx ON events (aggregate_id)`
- [ ] `upgrade()` creates index `events_aggregate_type_idx ON events (aggregate_type)`
- [ ] Both `CREATE INDEX` statements use `IF NOT EXISTS`
- [ ] `downgrade()` drops both indexes with `DROP INDEX IF EXISTS`
- [ ] `alembic upgrade head` runs without error on a live database
- [ ] `alembic downgrade -1` runs without error and removes both indexes

## Out of Scope
- Changing or removing the existing composite index `events_aggregate_idx`
- Adding indexes to any other table or column
- Query-level changes in application code

## Technical Context
- Migration files live in `db/migrations/versions/`
- Current head revision: `a3f8b2e1d9c0` (`fix_current_projects_view`)
- Existing pattern: `op.execute("""CREATE INDEX IF NOT EXISTS <name> ON events (<col>)""")` — follow this exactly
- Index naming convention observed in codebase: `<table>_<column>_idx`
- Database: Postgres on `127.0.0.1:5432`, database `ratchet`
- Run migrations with: `alembic upgrade head`

## Tasks
- [ ] Create a new migration file manually (e.g. `<hash>_add_events_aggregate_id_and_type_indexes.py`) with a fresh revision ID, `down_revision = "a3f8b2e1d9c0"`, and descriptive docstring
- [ ] Implement `upgrade()`: two `op.execute()` calls creating `events_aggregate_id_idx` and `events_aggregate_type_idx` using `CREATE INDEX IF NOT EXISTS`
- [ ] Implement `downgrade()`: two `op.execute()` calls dropping both indexes using `DROP INDEX IF EXISTS`
- [ ] Run `alembic upgrade head` to verify the migration applies cleanly
- [ ] Run `alembic downgrade -1` then `alembic upgrade head` again to verify round-trip

## Assumptions
- The `events` table already exists (created by `c67742c064f5`)
- No application query changes are needed — indexes are transparent to the ORM/store layer
- The development database is accessible via `DATABASE_URL` in the environment

## Verification Commands
```bash
# Apply migration
alembic upgrade head

# Verify indexes exist in postgres
psql $DATABASE_URL -c "\di events_aggregate_id_idx events_aggregate_type_idx"

# Test downgrade round-trip
alembic downgrade -1
psql $DATABASE_URL -c "\di events_aggregate_id_idx events_aggregate_type_idx"
alembic upgrade head

# Run unit tests to confirm nothing broken
pytest core/tests/ -v
```

## What Exists After This Spec
A new Alembic migration is the only artifact. The `events` table gains two single-column indexes (`events_aggregate_id_idx`, `events_aggregate_type_idx`) that accelerate queries filtering on `aggregate_id` or `aggregate_type` independently. All existing indexes and views remain unchanged.