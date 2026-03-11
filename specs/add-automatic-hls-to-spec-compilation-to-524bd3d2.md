# Spec N: Automatic HLS compilation in worker

## Objective
Extract the high-level spec compilation logic from `scripts/compile-feature.py` into `core/compiler.py`, expose a `compile_once()` pass in the worker, wire it into the notification loop with event-driven triggers (HLS added, task deployed), and add a startup catchup pass — so compilation happens automatically without human intervention.

## Success Criteria
- [ ] `core/compiler.py` exists and contains all reusable compilation logic extracted from the script
- [ ] `scripts/compile-feature.py` is a thin wrapper that imports from `core.compiler`
- [ ] `worker/runner.py` has a `compile_once(store)` function that iterates all active projects and features and compiles eligible HLS entries
- [ ] A new DB migration adds a `ratchet_compilation_trigger` Postgres NOTIFY channel fired on `high_level_spec.added` events and `task.status_changed` events where `to_status = 'deployed'`
- [ ] `worker/listener.py` LISTENs on both `ratchet_task_status` and `ratchet_compilation_trigger`; yields typed events that distinguish compilation triggers from task status events
- [ ] `notification_loop()` routes compilation trigger events to `compile_once(store)` only, not to `run_once`/`run_qa_once` (those still run when their own triggers fire)
- [ ] Startup catchup calls `compile_once` before `run_once` and `run_qa_once`
- [ ] Single-pass mode (`_main_async`) also calls `compile_once` before `run_once` and `run_qa_once`
- [ ] Unit tests in `core/tests/test_compiler.py` cover `is_eligible`, `extract_spec`, and `compile_all` with `InMemoryStore`
- [ ] Existing tests continue to pass

## Out of Scope
- Parallelising compilation across multiple HLS entries
- UI or API surface for triggering compilation manually
- Changes to the QA pipeline or deployment pipeline
- Modifying `compile-feature.py` CLI arguments

## Technical Context

**Current flow:** `scripts/compile-feature.py` is a standalone CLI script. It accepts `--feature-id`, loads the project, reads `docs/INTENT.md`, iterates HLS entries, calls `is_eligible()`, and for each eligible entry calls `compile_hls()` which invokes Claude, creates a task, assigns a spec, transitions the task to `ready_for_implementation`, propagates dependencies, and marks the HLS compiled via `FeatureManager.mark_compiled()`.

**Notification infrastructure:** `worker/listener.py` opens a dedicated psycopg connection and issues `LISTEN ratchet_task_status`. A DB trigger (`trg_notify_task_status`) fires on every `task.status_changed` event and calls `pg_notify('ratchet_task_status', ...)`. The listener yields `(task_id, status)` tuples filtered to `ready_for_implementation` and `ready_for_qa`.

**Trigger events for compilation:**
- `high_level_spec.added` — HLS with no dependencies is immediately eligible
- `task.status_changed` with `to_status = 'deployed'` — may unblock HLS whose dependencies are now met

**Chain:** `compile_hls()` transitions a task to `ready_for_implementation`, which fires the existing `trg_notify_task_status` trigger, which enqueues a dispatch in the worker that runs `run_once`. No extra wiring needed for newly compiled tasks to get picked up.

**Key files:**
- `scripts/compile-feature.py` — source of extraction
- `core/compiler.py` — new module (does not yet exist)
- `worker/runner.py` — adds `compile_once()`, updates `notification_loop()`, `_dispatch_one()`, `_main_async()`
- `worker/listener.py` — adds second LISTEN channel, changes yield type
- `db/migrations/` — new migration for compilation trigger
- `core/tests/test_compiler.py` — new test file
- `ratchet.yaml` — no change needed; `core/` is already covered by test/typecheck steps

## Tasks
- [ ] Create `core/compiler.py` with functions extracted verbatim from `scripts/compile-feature.py`: `run_claude`, `build_compile_prompt`, `extract_spec`, `get_task_status`, `is_eligible`, `compile_hls`. Add a new async function `compile_all(store: Store) -> int` that loads all active projects via `ProjectManager`, their features via `FeatureManager.list_features()`, their HLS via `FeatureManager.get_high_level_specs()`, calls `is_eligible()` for each, calls `compile_hls()` for eligible ones, and returns count of successfully compiled specs.
- [ ] Refactor `scripts/compile-feature.py` to import `run_claude`, `build_compile_prompt`, `extract_spec`, `is_eligible`, `compile_hls` from `core.compiler` instead of defining them locally. The `main()` function remains but uses imported symbols.
- [ ] Add `async def compile_once(store: Store) -> bool` to `worker/runner.py`. Calls `core.compiler.compile_all(store)`. Returns `True` if any HLS was compiled, `False` otherwise.
- [ ] Write DB migration in `db/migrations/versions/` that creates a PL/pgSQL function `notify_compilation_trigger()` and a trigger `trg_notify_compilation` on the `events` table (AFTER INSERT FOR EACH ROW). The function fires `pg_notify('ratchet_compilation_trigger', payload::text)` when `NEW.event_type = 'high_level_spec.added'` or when `NEW.event_type = 'task.status_changed'` and `NEW.payload->>'to_status' = 'deployed'`. Payload: `{"reason": "hls_added"|"task_deployed"}`. Include downgrade that drops trigger and function.
- [ ] Update `worker/listener.py`: in `__aenter__` add `LISTEN ratchet_compilation_trigger`. Change `listen()` yield type to `tuple[str, str, str]` where the first element is `"task"` or `"compile"`. For `ratchet_task_status` notifications yield `("task", task_id, status)`. For `ratchet_compilation_trigger` notifications yield `("compile", reason, "")`. Update `_ACTIONABLE_STATUSES` filter to only apply to task notifications.
- [ ] Update `notification_loop()` in `worker/runner.py`: change queue type to `asyncio.Queue[tuple[str, ...]]`. Update `_notification_producer` to pass through the new 3-tuple. In the consumer, dispatch based on first element: `"compile"` → `await compile_once(store)`, `"task"` → `await run_once(store, invoker)` + `await run_qa_once(store, invoker)`. Update startup catchup to call `compile_once(store)` before `_dispatch_one()`.
- [ ] Update `_dispatch_one()` to remain unchanged (still `run_once` + `run_qa_once`), but update the startup catchup call in `_run_loop()` to call `compile_once(store)` first.
- [ ] Update `_main_async()` to call `compile_once(store)` before `run_once` and `run_qa_once`.
- [ ] Write `core/tests/test_compiler.py` with unit tests using `InMemoryStore`: test `extract_spec` with and without marker, test `is_eligible` for uncompiled/no-deps (True), compiled (False), deps not deployed (False), deps deployed (True). Mock `compile_hls` and test `compile_all` iterates all projects and features.

## Assumptions
- `docs/INTENT.md` exists in every active project's `local_path`; `compile_all` logs a warning and skips projects where it is missing (matching current script behaviour)
- The `claude` binary is available in PATH when the worker runs; if not found, `compile_hls` returns False and logs an error
- Compilation is synchronous within `compile_once` (one HLS at a time, sequential, matching current script behaviour)
- `compile_once` is idempotent: `is_eligible` guards against recompiling already-compiled HLS

## Verification Commands
```bash
# Unit tests (no DB required)
.venv/bin/python -m pytest core/tests/test_compiler.py -v
.venv/bin/python -m pytest core/tests/ worker/tests/ -v

# Type check
.venv/bin/mypy core/ worker/

# Lint
.venv/bin/ruff check .

# Apply migration
.venv/bin/alembic -c db/alembic.ini upgrade head

# Smoke test: verify DB connection and trigger creation
.venv/bin/python db/smoke_test.py
```

## What Exists After This Spec
The worker automatically compiles eligible high-level specs into implementation-ready tasks whenever an HLS is added or a task is deployed. The compilation logic lives in `core/compiler.py` and is reusable both by the worker and the standalone CLI script. The worker startup always catches up on any compilations missed while it was offline.