# Consistent Materialized View Refresh

## Objective

Eliminate stale-view 404s in the web UI by (1) adding a 10-second periodic refresh background task to the web app lifespan and (2) calling `store.refresh_views()` after every dispatch in the worker notification loop.

## Success Criteria

- [ ] `web/app.py` lifespan starts an asyncio background task that calls `SELECT refresh_all_views()` every 10 seconds
- [ ] The background task is cancelled and awaited cleanly when the lifespan exits (no `asyncio.CancelledError` tracebacks on shutdown)
- [ ] `notification_loop` in `worker/runner.py` calls `store.refresh_views()` after startup catchup (after `_dispatch_one()` on line 554)
- [ ] `notification_loop` calls `store.refresh_views()` after every dequeued notification is processed (after `compile_once` or `_dispatch_one()` inside `_run_loop`)
- [ ] `store.refresh_views()` errors in the background task are logged as warnings and do not crash the web app
- [ ] `store.refresh_views()` errors in the worker are logged as warnings and do not crash the notification loop
- [ ] All existing tests pass: `.venv/bin/python -m pytest core/tests/ web/tests/ -v`
- [ ] Typecheck passes: `.venv/bin/mypy core/ worker/ web/`
- [ ] New tests cover: background task cancels cleanly, refresh errors are swallowed with a warning

## Out of Scope

- Do not switch to `REFRESH MATERIALIZED VIEW CONCURRENTLY`
- Do not add pg_cron or any database-level scheduler
- Do not add refresh calls to individual web route handlers
- Do not modify `core/store.py`, `InMemoryStore`, or `PostgresStore.refresh_views()`
- Do not modify database migrations or the `refresh_all_views()` SQL function
- Do not change the `Store` protocol

## Technical Context

- **Stack:** Python 3.12, FastAPI, asyncio, psycopg (async), pytest-asyncio
- **Web entry point:** `web/app.py` — FastAPI app with `lifespan` async context manager
- **Worker entry point:** `worker/runner.py` — `notification_loop()` function, lines 519–602
- **Store refresh method:** `core/store.py` — `PostgresStore.refresh_views()` calls `SELECT refresh_all_views()` via the pool; `InMemoryStore.refresh_views()` is a no-op
- **Existing background task pattern:** None in web app yet — this is the first one
- **Existing asyncio task cancellation pattern:** `worker/runner.py` lines 589–594 shows the cancel+await pattern used in `notification_loop`

## Related Files

- `web/app.py` — lifespan to modify (add background refresh task)
- `worker/runner.py:519–602` — `notification_loop` to modify (add `store.refresh_views()` calls)
- `worker/runner.py:540–543` — `_dispatch_one()` inner function (called during catchup and per notification)
- `worker/runner.py:549–575` — `_run_loop()` inner function (catchup + queue drain loop)
- `core/store.py` — `Store` protocol and `PostgresStore.refresh_views()` (read only, do not modify)
- `web/tests/` — add new test file `web/tests/test_lifespan.py`
- `worker/tests/` — add tests to `worker/tests/test_notification_loop.py` or new file

## Tasks

- [ ] **Modify `web/app.py` lifespan** — add periodic refresh background task:
  - Inside the `lifespan` async context manager, after `app.state.store = PostgresStore(pool)`, start an asyncio task that loops: `await asyncio.sleep(10)` then `await app.state.store.refresh_views()` with a `try/except Exception` that logs warnings
  - Store the task reference; cancel and await it in the cleanup section (after `yield`, before `await close_pool()`)
  - Import `asyncio` at the top of the file
  - The loop must handle `asyncio.CancelledError` by re-raising (not swallowing it) so cancellation propagates correctly

- [ ] **Modify `worker/runner.py` `_run_loop()`** — add refresh after startup catchup:
  - After `await _dispatch_one()` on the startup catchup (line 554), add `await store.refresh_views()` wrapped in `try/except Exception` that logs a warning

- [ ] **Modify `worker/runner.py` `_run_loop()`** — add refresh after each dequeued notification:
  - In the `finally` block of the notification processing (lines 573–575), after `queue.task_done()`, add `await store.refresh_views()` wrapped in `try/except Exception` that logs a warning

- [ ] **Write tests for web app periodic refresh** in `web/tests/test_lifespan.py`:
  - Test: background refresh task is created and calls `refresh_views` at least once within interval
  - Test: refresh errors are caught and do not propagate (mock `refresh_views` to raise, verify no crash)
  - Test: background task is cancelled on lifespan exit (no dangling tasks)
  - Use `InMemoryStore` or mock the store; do not require a real DB

- [ ] **Write tests for worker refresh calls** in `worker/tests/test_notification_loop_refresh.py` or extend existing test file:
  - Test: `store.refresh_views()` is called after startup catchup completes
  - Test: `store.refresh_views()` is called after a "task" notification is processed
  - Test: `store.refresh_views()` is called after a "compile" notification is processed
  - Test: refresh errors in notification loop are swallowed (mock `refresh_views` to raise, verify loop continues)

## Implementation Pattern

### `web/app.py` lifespan (target shape):

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import asyncio
    from core.db import close_pool, get_pool

    pool = await get_pool()
    app.state.pool = pool
    app.state.store = PostgresStore(pool)

    async def _refresh_loop() -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await app.state.store.refresh_views()
            except Exception:
                logger.warning("Periodic view refresh failed", exc_info=True)

    refresh_task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        await close_pool()
```

### `worker/runner.py` `_run_loop()` changes (target shape):

```python
async def _run_loop() -> None:
    nonlocal active
    # Startup catchup
    logger.info("Worker: running startup catchup")
    await compile_once(store)
    await _dispatch_one()
    try:
        await store.refresh_views()
    except Exception:
        logger.warning("View refresh failed after startup catchup", exc_info=True)

    while True:
        event_tuple = await queue.get()
        # ... existing logging ...
        active = True
        try:
            if kind == "compile":
                await compile_once(store)
            else:
                await _dispatch_one()
        finally:
            active = False
            queue.task_done()
            try:
                await store.refresh_views()
            except Exception:
                logger.warning("View refresh failed after notification", exc_info=True)
```

## Assumptions

- `asyncio` is already available (stdlib); no new dependencies needed
- `PostgresStore.refresh_views()` is safe to call concurrently with web requests (it acquires a pool connection independently)
- 10-second interval is acceptable staleness for the web UI
- The background task sleeping first (sleep before refresh) means views are NOT refreshed immediately on startup — the existing startup catchup in the worker handles that

## Verification Commands

```bash
# Run all tests
.venv/bin/python -m pytest core/tests/ web/tests/ -v

# Typecheck
.venv/bin/mypy core/ worker/ web/

# Lint
nix-shell -p python312Packages.ruff --run "ruff check ."

# Manual smoke test: start web app, create a task via script, check it appears within 10s
DATABASE_URL=postgresql+psycopg://ratchet@127.0.0.1:5432/ratchet .venv/bin/python -m web &
sleep 2
# In another terminal: python scripts/add-task.py --project-id <uuid> --title "Test refresh"
# Then within 10 seconds: curl http://localhost:8000/ should show the new task
kill %1
```
