```
COMPLETED: specs/07-invoker.md

Tasks completed: 9/9
Verification:
  ruff check .: PASS
  pytest core/tests/ -v: PASS (115 passed)
  alembic upgrade head: N/A — spec explicitly excludes DB schema changes

Files created/modified:
- core/invoker.py — InvocationResult dataclass, get_traces_dir(), parse_output(), ClaudeCodeInvoker
- core/tests/test_invoker.py — 27 unit tests covering all 4 outcome cases, trace file writing, get_traces_dir() env var/default behavior, failure reason extraction, subprocess args validation
```
