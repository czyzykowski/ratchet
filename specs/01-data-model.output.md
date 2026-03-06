All files are in place. Here's a summary of everything completed:

**Created/updated files:**

| File                                                                | Description                                                                            |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `.env.example`                                                      | Updated with both `DATABASE_URL` and `TEST_DATABASE_URL` (explicit connection strings) |
| `flake.nix`                                                         | Added `TEST_DATABASE_URL` warning to shellHook                                         |
| `db/alembic.ini`                                                    | Alembic config with `script_location = %(here)s/migrations`                            |
| `db/migrations/env.py`                                              | Reads `DATABASE_URL` from environment                                                  |
| `db/migrations/versions/c67742c064f5_initial_schema.py`             | Creates `events` table + 4 materialized views                                          |
| `db/migrations/versions/21beadfdc614_add_refresh_views_function.py` | Adds `refresh_all_views()` SECURITY DEFINER function + grants for `ratchet_test`       |
| `db/smoke_test.py`                                                  | Appends 3 events, calls `refresh_all_views()`, asserts 1 row in `current_tasks`        |
| `docs/INTENT.md`                                                    | Project intent statement                                                               |
| `docs/CLAUDE.md`                                                    | Technical entry point with file map and commands                                       |

**Verification results:**

- `alembic upgrade head` — ran both migrations successfully
- `python db/smoke_test.py` — passed: `OK: task ... | title='smoke task' | status='spec_qa'`
- `ruff check .` — all checks passed

**Note on `refresh_all_views()`:** The smoke test calls `SELECT refresh_all_views()` rather than `REFRESH MATERIALIZED VIEW` directly, because PostgreSQL restricts view refresh to the owner. The SECURITY DEFINER function (owned by `ratchet`) allows `ratchet_test` to trigger refreshes. The `core/store.py` refresh still works fine using direct `REFRESH MATERIALIZED VIEW CONCURRENTLY` since it runs as the `ratchet` owner.
