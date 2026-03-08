# Spec: Worker continuous loop — poll ready_for_implementation and ready_for_qa tasks

## Objective

Make `python -m worker` run indefinitely, polling for both `ready_for_implementation` and `ready_for_qa` tasks on every iteration. Add a `--once` flag to preserve the existing single-pass exit behavior used by `scripts/run-next.py`.

## Success Criteria
- [ ] `python -m worker` runs indefinitely, processing tasks until interrupted with Ctrl+C
- [ ] Each loop iteration calls `run_once()` then `run_qa_once()` in sequence
- [ ] When at least one task was processed (either function returned `True`), the next iteration starts immediately with no sleep
- [ ] When no tasks are found, the worker sleeps 30 seconds before the next iteration
- [ ] `python -m worker --once` calls `run_once()` then `run_qa_once()` exactly once, then exits (same behaviour as today's `main()`)
- [ ] `scripts/run-next.py` continues to work without modification — it imports `main as worker_main` from `worker.runner`, which must keep its existing single-pass behaviour
- [ ] Ctrl+C exits cleanly with a log message and no stack trace printed to the user
- [ ] Unit tests cover: loop exits on `KeyboardInterrupt`, sleep is skipped when a task is processed, `--once` path executes both functions exactly once

## Out of Scope
- Configurable poll interval (hardcoded 30 s)
- New task statuses or DB migrations
- Changes to any `core/` module
- Parallel execution of implementation and QA tasks

## Technical Context

- `worker/runner.py` — `run_once(store, invoker) -> None` (line 128) and `run_qa_once(store, invoker) -> None` (line 289) both return `None` today regardless of whether a task was found. Both must be changed to return `bool`.
- `run_once` logs `"No tasks ready for implementation. Exiting."` (line 145); `run_qa_once` logs `"No QA tasks ready. Exiting."` (line 307) — both "Exiting." suffixes are misleading in a loop and must be removed.
- `main()` (line 400) and `_main_async()` (line 408) run `run_once` only — `run_qa_once` is never called here.
- `worker/__main__.py` is a 3-line file: `from worker.runner import main; main()` — needs `argparse`.
- `scripts/run-next.py` (line 14): `from worker.runner import main as worker_main; worker_main()` — `main()` must keep single-pass semantics (call both `run_once` and `run_qa_once` once, then exit).
- Existing tests: `worker/tests/test_runner.py` and `worker/tests/test_run_qa.py` — new tests can go in either file or a new `test_loop.py`.

## Tasks
- [ ] Change `run_once(store, invoker) -> None` to `-> bool`: return `True` if a task was found and processed (task was not `None` from `get_next_task`), `False` otherwise
- [ ] Change `run_qa_once(store, invoker) -> None` to `-> bool`: return `True` if a QA task was found and processed, `False` otherwise
- [ ] Update log message in `run_once` from `"No tasks ready for implementation. Exiting."` to `"No tasks ready for implementation."`
- [ ] Update log message in `run_qa_once` from `"No QA tasks ready. Exiting."` to `"No QA tasks ready."`
- [ ] Update `_main_async()` in `worker/runner.py` to call both `await run_once(store, invoker)` and `await run_qa_once(store, invoker)` (single-pass, both steps — preserves `main()` / `run-next.py` behaviour)
- [ ] Add `main_loop(store: Store, invoker: ClaudeCodeInvoker) -> None` async function to `worker/runner.py`: loops forever, each iteration awaits `run_once()` then `run_qa_once()`, sleeps 30 s only when both return `False`, catches `KeyboardInterrupt` and logs `"Worker stopped."` before returning cleanly
- [ ] Add `_main_loop_async() -> None` and `main_loop_entry() -> None` to `worker/runner.py` mirroring the `_main_async` / `main` pattern: initialises `PostgresStore`, calls `main_loop`, closes pool in `finally`
- [ ] Rewrite `worker/__main__.py` to parse a single `--once` flag with `argparse`: without `--once` call `main_loop_entry()`; with `--once` call `main()` (existing single-pass)
- [ ] Add tests in `worker/tests/test_loop.py` using `InMemoryStore`:
  - `run_once` returns `True` when a task is found, `False` when none
  - `run_qa_once` returns `True` when a QA task is found, `False` when none
  - `main_loop` exits after `KeyboardInterrupt` without raising
  - Loop skips sleep when `run_once` returns `True`
- [ ] Update `CHANGELOG.md` under `[Unreleased]` → `Changed`: `worker runs in continuous polling loop by default (30 s idle sleep); add --once flag for single-pass exit`

## Assumptions
- `KeyboardInterrupt` is caught at the top of `main_loop`, not inside `run_once`/`run_qa_once`
- `run_once` returning `True` means a task tuple was found (regardless of whether implementation ultimately succeeded or failed — the worker did useful work)
- `asyncio.sleep(30)` is used for the idle wait; it is interruptible by `KeyboardInterrupt` propagating through `asyncio.run`
- 30 s is non-configurable for now

## Verification Commands
```bash
# Unit tests (no DB required)
pytest worker/tests/ -v

# Manual: start loop — verify "No tasks ready" log repeats every 30 s, exits cleanly on Ctrl+C
python -m worker

# Manual: single-pass exits immediately
python -m worker --once

# Existing script still works
python scripts/run-next.py
```

## What Exists After This Spec

`python -m worker` is a long-running daemon that continuously advances tasks through `ready_for_implementation` → QA → `ready_for_deployment` without operator intervention between steps. The worker can be left running and will pick up new tasks as they are enqueued. `scripts/run-next.py` and `--once` mode remain available for scripted single-pass use.