**Note:** No `.env` file exists — used `.env.example` defaults for verification.

---

```
COMPLETED: specs/02-state-machine.md

Tasks completed: 12/12
Verification:
  ruff check .:         PASS
  pytest core/tests/ -v: PASS (16/16)
  python db/smoke_test.py: PASS

Files created/modified:
- core/store.py         — Store protocol, InMemoryStore, PostgresStore (lazy DB import)
- core/state_machine.py — InvalidTransitionError, TaskStateMachine
- core/tests/__init__.py
- core/tests/test_state_machine.py — 16 tests, no DB required
- db/smoke_test.py      — updated to use PostgresStore + TaskStateMachine
```

**Key decisions:**
- `core/store.py` uses a lazy `from core.db import get_pool` inside `PostgresStore._get_pool()` so `psycopg`/`libpq` are never imported during unit test collection — all tests pass with no database connection.
- `PostgresStore` accepts an optional `pool` parameter, letting `smoke_test.py` inject its own `TEST_DATABASE_URL`-connected pool.
- `TASK_STATUS_CHANGED` payload includes `"status": new_status` alongside `from_status`/`to_status` so the `current_tasks` materialized view (which reads `payload->>'status'`) correctly reflects the transition.
