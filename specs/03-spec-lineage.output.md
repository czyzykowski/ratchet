```
COMPLETED: specs/03-spec-lineage.md

Tasks completed: 9/9
Verification:
  ruff check .: PASS
  pytest core/tests/ -v: PASS (30 passed, 14 new + 16 existing)
  alembic upgrade head: PASS
  python db/smoke_test.py: PASS

Files created/modified:
- core/spec_manager.py
- core/tests/test_spec_manager.py
```

**Notes:**
- No `.env` file present — used `.env.example` defaults (`DATABASE_URL`, `TEST_DATABASE_URL`) for alembic and smoke_test runs.
- `SpecManager` uses a dual-write pattern: `SPEC_CREATED` events are stored under both `aggregate_id=spec_id, aggregate_type="spec"` (for `get_spec()` lookups) and `aggregate_id=task_id, aggregate_type="task_spec"` (for `get_spec_lineage()` lookups). This keeps both query paths O(1) aggregate lookups without breaking the `Store` protocol.
- Lineage reconstruction uses the head-find + backwards-walk algorithm, ensuring correct ordering regardless of event insertion order.
