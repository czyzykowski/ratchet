# Spec: Watchdog — detect and surface silent executions

## Objective
Switch `ClaudeCodeInvoker.invoke()` from `subprocess.run(capture_output=True)` to `subprocess.Popen` with line-by-line stdout/stderr reading. Add a watchdog background thread that prints a stderr warning when no output has been received for a configurable interval (default 5 minutes). Wire a `--watchdog-timeout` CLI flag into the worker entry point.

## Success Criteria
- [ ] `ClaudeCodeInvoker.__init__` accepts an optional `watchdog_timeout: int = 300` parameter (seconds)
- [ ] `invoke()` uses `subprocess.Popen` and reads stdout/stderr incrementally; all lines are accumulated and the final output string is identical in content to what was captured before
- [ ] A daemon watchdog thread starts when the subprocess starts; it wakes every 30 s and prints to `sys.stderr` if `now - last_activity_ts > watchdog_timeout`
- [ ] Warning message format: `[watchdog] task=<execution_id> silent for <N>s (threshold <T>s)`
- [ ] Warning repeats each time the threshold is crossed again (i.e. at T, 2T, 3T, …) — implemented by tracking last-warned timestamp separately from last-activity timestamp
- [ ] Watchdog thread is a daemon thread and is stopped (event set) after the subprocess exits
- [ ] `worker/__main__.py` adds `--watchdog-timeout` flag (int, default 300) and passes it to `ClaudeCodeInvoker(watchdog_timeout=...)`
- [ ] All existing `TestClaudeCodeInvoker` tests continue to pass (they mock `subprocess.run`; update mocks to `subprocess.Popen` as needed)
- [ ] New unit tests cover: warning fires after threshold, warning repeats, no warning when output is active, watchdog thread stops after process exits
- [ ] `pytest core/tests/ -v` passes

## Out of Scope
- Automatic killing of the stuck process
- Storing warnings as database events
- Watchdog for the QA review `subprocess.run` call in `runner.py`

## Technical Context
- `core/invoker.py` line 98: `subprocess.run(capture_output=True, text=True)` — this is what switches to `Popen`
- `worker/__main__.py` line 5–16: argument parsing; `ClaudeCodeInvoker()` is instantiated inside `runner._main_async()` and `runner._main_loop_async()` — those need to accept and forward `watchdog_timeout`
- `runner.py` lines 444, 458: `ClaudeCodeInvoker()` instantiated with no args — update to accept optional `watchdog_timeout` forwarded from `__main__`
- Watchdog thread: use `threading.Thread(daemon=True)` + `threading.Event` for stop signal; sleep loop with 30 s intervals so it reacts quickly without busy-waiting
- `last_activity_ts` is a `float` (from `time.monotonic()`) updated inside the Popen read loop; shared with the watchdog thread — use `threading.Lock` or `time.monotonic()` atomic reads (float assignment is GIL-safe in CPython, but a lock is safer and explicit)
- Existing tests mock `subprocess.run`; switch to mocking `subprocess.Popen` using `unittest.mock.MagicMock` with a `stdout` iterable and `wait()` returning a returncode

## Tasks
- [ ] In `core/invoker.py`: add `watchdog_timeout: int = 300` to `ClaudeCodeInvoker.__init__` and store as `self.watchdog_timeout`
- [ ] In `core/invoker.py`: rewrite `invoke()` to use `subprocess.Popen(cmd, cwd=..., stdout=PIPE, stderr=PIPE, text=True)`, read lines from stdout and stderr in a loop, accumulate into `output` string, update `last_activity_ts` on each line; after process finishes call `proc.wait()` to get returncode
- [ ] In `core/invoker.py`: implement `_watchdog_loop(execution_id, get_activity, stop_event, threshold)` function — runs in a thread, wakes every 30 s, prints warning to stderr when inactive beyond threshold, tracks last-warned time to allow repeating warnings
- [ ] In `core/invoker.py`: start watchdog thread before Popen, stop it (set stop event, join) after `proc.wait()` returns
- [ ] In `worker/runner.py`: add `watchdog_timeout: int = 300` parameter to `_main_async()` and `_main_loop_async()`; pass to `ClaudeCodeInvoker(watchdog_timeout=watchdog_timeout)`
- [ ] In `worker/__main__.py`: add `parser.add_argument("--watchdog-timeout", type=int, default=300, metavar="SECONDS", help="Seconds of silence before watchdog warning (default: 300)")` and pass `args.watchdog_timeout` into `main()` / `main_loop_entry()` (update those signatures accordingly)
- [ ] In `core/tests/test_invoker.py`: update existing `ClaudeCodeInvoker` tests to mock `subprocess.Popen` instead of `subprocess.run`
- [ ] In `core/tests/test_invoker.py`: add `TestWatchdog` class with tests: warning fires, warning repeats, no warning when active, thread stops after process

## Assumptions
- CPython GIL makes `float` assignment to a shared variable safe without a lock, but a `threading.Lock` will be used for explicitness
- stdout and stderr are read sequentially (stdout first, then stderr via `communicate()`), or interleaved via two threads — simplest correct approach is `proc.communicate()` for stderr after reading stdout, or use two `threading.Thread` readers; choose the two-reader approach to avoid deadlock on large stderr
- The warning threshold clock resets each time a new line is received, not on a fixed schedule

## Verification Commands
```bash
# Unit tests (no DB required)
pytest core/tests/test_invoker.py -v

# Full unit test suite
pytest core/tests/ -v

# Confirm CLI flag is wired
.venv/bin/python -m worker --help | grep watchdog
```

## What Exists After This Spec
`ClaudeCodeInvoker` streams Claude output line-by-line and monitors for silence. If a claude subprocess stalls with no output for 5+ minutes, the worker prints a timestamped warning to stderr. The threshold is tunable via `--watchdog-timeout`. All prior invoker behaviour (trace writing, COMPLETED/BLOCKED detection, result structure) is unchanged.