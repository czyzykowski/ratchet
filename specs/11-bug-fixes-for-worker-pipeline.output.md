```
COMPLETED: specs/11-bug-fixes-for-worker-pipeline.md
Tasks completed: 9/9
Verification:
  ruff check .: PASS
  pytest core/tests/ worker/tests/ -v: PASS (129 tests)
  .venv/bin/python scripts/review-blocked.py: PASS — shows "Last failure: no completion marker found in output"
Files created/modified:
- core/invoker.py (added assert to verify traces_dir exists in __init__)
- core/context_assembler.py (added _COMPLETION_INSTRUCTIONS section to build_prompt)
- core/tests/test_context_assembler.py (added 2 new tests for COMPLETED:/BLOCKED: in prompt)
- scripts/review-blocked.py (fixed SQL to find execution.failed via execution.started subquery)
- worker/runner.py (added RATCHET_DEBUG=1 debug output before and after invocation)
Commit: aed33ea
```
