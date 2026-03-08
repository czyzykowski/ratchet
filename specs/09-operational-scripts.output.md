```
COMPLETED: specs/09-operational-scripts.md
Tasks completed: 14/14
Verification:
  alembic upgrade head: PASS (migration a3f8b2e1d9c0 applied)
  python db/smoke_test.py: PASS
  ruff check .: PASS (All checks passed)
  pytest core/tests/ worker/tests/ -v: PASS (127 passed)
  python scripts/board.py: PASS (=== RATCHET BOARD === / No tasks found.)
  python scripts/add-project.py --name ratchet --path $(pwd): PASS (Project ID: 832cc13f-...)
Files created/modified:
- db/migrations/versions/a3f8b2e1d9c0_fix_current_projects_view.py
- scripts/add-project.py
- scripts/add-task.py
- scripts/add-spec.py
- scripts/run-next.py
- scripts/board.py
- scripts/review-blocked.py
- CLAUDE.md (updated with scripts docs and full workflow)
Commit: 3bf60f2
```
