# Changelog

## [Unreleased]

### Added
- Execution timeout reaper: periodic cleanup (every 5 min) marks running executions older than 2 hours with no connected worker as failed
- QA failure classifier (`orchestrator/failure_classifier.py`): classifies failures as `code`, `infra`, or `system`; infra errors skip fix attempts and immediately block with `[INFRA]` prefix
- Startup execution recovery: on orchestrator restart, all running executions with no connected worker are marked failed
- Line-anchored marker detection helpers (`has_completed_marker`, `has_blocked_marker`) in `core/invoker.py`

### Changed
- COMPLETED/BLOCKED marker detection now uses line-anchored regex instead of substring matching, preventing false positives from subprocess output (e.g., npm install logs)
- Sequencer QA pipeline uses `classify_qa_failure()` before attempting fix loop — infrastructure errors are immediately blocked without wasting retry attempts

### Fixed
- False BLOCKED detection from subprocess output containing "BLOCKED" as substring
- Sequencer impl/QA test failures: mock `_apply_patch_to_local` to avoid filesystem access, add `get_diff` handler to QA mock, fix task state setup and event assertion

### Changed
- `scripts/diagnose.py` upgraded to single operational tool: `--fix-orphans` per-task support, `--unblock-infra` bulk unblock, `--check-false-positives` trace scanning, execution waste analysis, per-task failure breakdown, false-positive BLOCKED detection in task deep-dive, proper view refresh after fixes
- Feature pages normalized: consistent `badge badge-${status}` pattern across FeaturesPage, ProjectPage features section, and FeatureDetailPage; feature statuses added to STATUS_COLORS; spec cards use colored left borders; progress section uses primary accent; description and dependency text use design tokens instead of hardcoded colors
- CSS design token system: 90+ custom properties in `:root` for colors, shadows, overlays
- `ErrorBoundary` component wrapping the app to catch React crashes with reload button
- `:focus-visible` styles for all interactive elements (buttons, task cards, toasts, rows)
- `prefers-reduced-motion` media query disabling all animations
- ARIA `role="dialog"` and `aria-modal="true"` on all modal overlays with `aria-label="Close"` on close buttons
- Keyboard support (Enter/Space) on `TaskCard` and `Toast` components
- `aria-expanded` and keyboard support on collapsible Features section in ProjectPage
- Tablet breakpoint at 768px with intermediate layout adjustments
- Utility CSS classes: `link-soft`, `text-dim`, `text-secondary`, `dark-surface`, `abandon-section`
- Lazy-loaded mermaid plugin in Markdown component (only loads when content contains mermaid blocks)
- `scripts/diagnose.py` — comprehensive task diagnostic: blocked tasks with failure classification, orphaned execution detection, dependency chain analysis, stale task detection; supports `--fix-orphans` and `--task-id` modes
- `<main>` landmark wrapping Routes for screen reader navigation
- `loading="lazy"` on all chat inline images

### Changed
- Extracted all hard-coded CSS colors into `:root` custom properties (234 var() references, zero hex outside tokens)
- Board columns stack vertically at 600px for phones, shrink to 240px on tablets
- 44px minimum touch targets on buttons at all screen sizes (not just 430px)
- Replaced inline `color: '#7eb8f7'` styles with `link-soft` CSS class across detail pages
- Replaced inline dark surface styles with `dark-surface` CSS class
- Replaced hardcoded abandon section colors with `--color-danger` family tokens
- Polling queries (`useWorkerStatus`, connected workers) now pause when tab is hidden via `refetchIntervalInBackground: false`

### Fixed
- Contrast ratio failures: darkened text-tertiary, text-muted, text-dim and 4 badge foreground colors to meet WCAG AA 4.5:1
- Blob URL memory leak in chat components: URLs now revoked on success path (not just error path)
- Generic image alt text: "attached" → "User uploaded image", "pending attachment" → "Image pending upload"
- Merge conflicts on remote worker tasks: `_apply_patch_to_local` now uses the original base commit instead of HEAD, preventing context mismatch when develop advances between execution and merge
- Dispatcher tests updated to match `dispatch_pending` returning `list[DispatchResult]` instead of `int`

### Changed
- Architecture session system prompt now follows a multi-phase flow (Discover → Analyze → Recommend → Act)

### Added
- Scoped architecture session support — scope string now generates focused exploration instructions in system prompt
- `add_hls` action in action executor for creating high-level specs on features from architecture sessions
- Architecture session backend: CRUD endpoints and SSE streaming at `/api/architecture-sessions`
- `ChatSessionSummary`, `TaskSummary`, `FeatureSummary` Pydantic models in `core/models.py`
- `get_chat_sessions_for_project`, `get_tasks_summary_for_project`, `get_features_summary_for_project` query functions in `web/queries.py`
- Project chat session API endpoints (`/api/project-chat-sessions`)
- `core/event_queries.py` — `has_pending_baseline_qa_failure()` migrated from `worker/event_helpers.py`

### Removed
- `worker/dispatcher.py` — `ProjectDispatcher`, `DispatchResult` (replaced by `orchestrator/dispatcher.py`)
- `worker/pipelines/` — `ImplPipeline`, `QAPipeline`, `MergePipeline` (replaced by `orchestrator/sequencer.py`)
- `worker/task_finder.py` — task discovery (replaced by orchestrator dispatch logic)
- `worker/event_helpers.py` — stateless helpers (`has_pending_baseline_qa_failure` moved to `core/event_queries.py`)
- `worker/runner.py` — `notification_loop`, `get_next_task`, `run_once` (replaced by orchestrator)
- `worker/service.py` — `WorkerService` wrapping `notification_loop` (replaced by `LocalWorkerManager`)
- `worker/listener.py` — Postgres LISTEN/NOTIFY (only used by `notification_loop`)
- `worker/__init__.py` exports: `WorkerService`, `WorkerSettings` removed; only `LogBuffer`, `LogEntry` remain
- `worker/__main__.py` — `--once` mode removed; `--remote` is now required
- `web/routes/api/worker.py` — `POST /worker/run-next` endpoint removed (dispatch is automatic via orchestrator)
- `web/routes/worker.py` — `POST /worker/run-next` HTML route removed
- `scripts/run-next.py` — manual dispatch trigger removed (orchestrator dispatch loop runs automatically)
- `scripts/run-qa.py` — manual QA trigger removed (QA is driven by orchestrator pipeline)
- Old worker tests: `test_loop`, `test_notification_loop_refresh`, `test_dispatcher`, `test_runner`, `test_dispatch_merge`, `test_merge_once`, `test_run_qa`, `test_poll_pr_merges`, `test_module_imports`, `test_service`, `test_listener`

### Added
- Crash recovery: orchestrator replays events on startup to find orphaned in-progress tasks and resets them after a configurable grace period (`RECOVERY_GRACE_PERIOD_SECONDS`, default 60s)
- Disconnect grace period: workers have a configurable timeout (`WORKER_RECONNECT_TIMEOUT_SECONDS`, default 30s) to reconnect before their in-progress task is reset
- `TASK_ASSIGNED_TO_WORKER` events now record the real `execution_id` (not `"pending"`) on all pipeline types (impl, QA, merge)
- `get_in_progress_task_ids()` in `web/queries.py` queries the `current_tasks` materialized view for tasks in `in_progress` status
- `web/routes/api/ws_worker.py` — `/ws/worker` WebSocket endpoint for worker registration and message handling, moved from `orchestrator/server.py`
- `web/local_worker.py` — `LocalWorkerManager` spawns and manages a local `python -m worker --remote` subprocess; replaces embedded `WorkerService`
- `web/routes/api/workers.py` — `GET /api/workers` returns all connected workers from `WorkerRegistry`
- `GET /api/worker/status` now includes `pid` field for the local worker subprocess
- `orchestrator/registry.py`, `dispatcher.py`, `sequencer.py`, `channel.py` retained as library code; `orchestrator/__main__.py` and `orchestrator/server.py` removed
- Dispatch loop (`dispatch_loop`) runs as async task in web app lifespan, controlled by `DISPATCH_ENABLED` env var
- `WorkerRegistry` stored on `app.state.registry`, shared between WebSocket endpoint and dispatch loop
- `python -m web` now accepts `--port` and `--dispatch/--no-dispatch` flags

### Changed
- `python -m web` starts everything: web API, `/ws/worker` WebSocket, dispatch loop, and local worker subprocess
- Embedded in-process `WorkerService` replaced by `LocalWorkerManager` (subprocess-based)
- Worker settings env vars changed: `WORKER_CAPABILITIES`, `WORKER_ENABLED`, `WEB_PORT` (removed `WORKER_WATCHDOG_TIMEOUT`, `WORKER_MAX_WORKERS`)
- SPA workers page now shows connected worker table (from `GET /api/workers`) with auto-refresh every 5 seconds

### Removed
- `orchestrator/__main__.py` — orchestrator is no longer a separate process
- `orchestrator/server.py` — server functionality merged into web process
- `orchestrator/tests/test_server.py` — covered by new `web/tests/test_ws_worker.py`

- Stateless command-executing remote worker (`worker/executor.py`, `worker/remote.py`) implementing full command protocol
- `current_tasks` materialized view now includes `depends_on` JSONB column (migration `d5e6f7a8b9c0`)
- `web/queries.py`: `get_task_detail()`, `get_board_tasks()`, `get_task_baseline_qa_failure()`, `get_task_pr_and_deploy_info()`, `get_task_feature_backlink()` — direct materialized view queries replacing event-replay managers

### Changed
- GET `/api/tasks/{task_id}` uses `queries.get_task_detail()` instead of 5+ manager round trips
- GET `/api/board` uses `queries.get_board_tasks()` (1 SQL query) instead of N event replays via `board_builder.load_board()`
- `PostgresStore.append_event()` now calls `refresh_views()` after every event insert so materialized views are always current

- `core/claude_subprocess.py` — unified Claude subprocess invocation with `run()` (blocking) and `start()` (non-blocking with activity tracking)

### Changed
- Capability matching now merges task + project `required_capabilities` at dispatch time via `worker/capability_check.py`
- `core/invoker.py` uses `claude_subprocess.start()` instead of raw `subprocess.Popen`
- `core/compiler.py` uses `claude_subprocess.run()` instead of raw `subprocess.Popen`
- `core/review_engine.py` uses `claude_subprocess.run()` instead of raw `subprocess.run`; removed `USE_NIX_DEVELOP` env var
- `worker/pipelines/qa.py` uses `claude_subprocess.run()` for Claude review step
- Decomposed `worker/dispatcher.py` (941 lines) into pipeline modules: `worker/pipelines/impl.py`, `worker/pipelines/qa.py`, `worker/pipelines/merge.py`
- Extracted `worker/worktree.py` (worktree lifecycle helpers), `worker/event_helpers.py` (event query helpers), `worker/task_finder.py` (TaskFinder class)
- `worker/dispatcher.py` is now a 168-line facade delegating to pipeline classes
- Updated all test patch targets to reference new module paths

### Added
- `core/managers.py` — `Managers` facade that constructs all store-backed managers from a single `Store` instance; `app.state.managers` set on web startup

### Changed
- `ProjectDispatcher` uses `Managers` facade internally instead of constructing 4 managers separately
- Extracted `ProjectDispatcher` class into `worker/dispatcher.py` — owns task discovery, priority dispatch (merge > QA > impl), and manager construction
- `worker/runner.py` reduced from 1206 to 386 lines — retains only notification loop, CLI entry points, and backwards-compatible wrappers
- Deduplicated project→task discovery loop (was copy-pasted 5 times) into `ProjectDispatcher._find_tasks()`

### Fixed
- Updated all test patch targets from `worker.runner.*` to `worker.dispatcher.*` for functions that moved to the dispatcher module
- Fixed `merge_once` backwards-compat wrapper returning `True` on merge failure (now returns `False` as the original did)
- Fixed `get_next_task` backwards-compat wrapper using hardcoded `"deployed"` string instead of `ev.DEPLOYED` constant for dependency checks
- Updated `scripts/board.py` to import `_has_pending_baseline_qa_failure` from `worker.dispatcher`

### Added
- `Project` model gains `required_capabilities: list[str] = []` field; stored in `PROJECT_CREATED` / `PROJECT_UPDATED` event payloads
- `TASK_CAPABILITIES_UPDATED` event type in `core/events.py`; `TaskManager.update_task_capabilities()` method
- `TaskManager.create_task()` accepts `project_capabilities` parameter and unions it with task-level `required_capabilities` (deduplicated)
- `TaskManager._replay_task()` handles `TASK_CAPABILITIES_UPDATED` by overwriting `required_capabilities`
- `scripts/add-project.py` gains `--capabilities` flag (comma-separated)
- `scripts/update-project.py`: new script with `--project-id` and `--capabilities` flags
- `POST /api/projects` and `PATCH /api/projects/{project_id}` accept `required_capabilities`
- `PATCH /api/tasks/{task_id}` accepts `required_capabilities` to call `TaskManager.update_task_capabilities()`
- `POST /api/tasks` fetches project capabilities and passes them to `TaskManager.create_task()`
- TypeScript `Project` interface and `createProject()` options include `required_capabilities`
- `NewProjectModal` and `ProjectSettingsModal` include a comma-separated capabilities input
- Task detail page displays and allows inline editing of `required_capabilities`
- Alembic migration `d1e2f3a4b5c6` adds `required_capabilities` JSONB column to `current_projects` view and updates `current_tasks` view to prefer `TASK_CAPABILITIES_UPDATED` over `TASK_CREATED`
- Unit tests for project capabilities (create, update, replay) and task capability merging and editing
- Web API tests for create/update project with capabilities and task capability update endpoint
- `NewTaskModal` displays a "Required Capabilities" input pre-populated with project defaults; submitted capabilities are sent to `POST /api/tasks` as `required_capabilities`
- `POST /api/tasks` accepts optional `required_capabilities`; when provided, uses them as-is without merging project defaults
- Unit tests for `merge_once` edge cases: `read_intent` exception fallback, no execution branch, `config_source == "db"` ratchet_yaml branching, and missing `spec_id` handling
- Integrated `merge_once` into worker dispatch cycle — tasks in local deployment mode auto-merge after QA passes; `merge_once` now accepts optional `project_id` parameter for per-project scoping
- Updated CLAUDE.md: documented embedded worker architecture, `worker/service.py` and `worker/log_buffer.py` in repo structure, `python -m web` entry point, `WORKER_*` env vars, and `app.state.worker_service` key pattern
- `web/app.py` auto-starts embedded `WorkerService` on lifespan startup and stops it on shutdown; reads `WORKER_ENABLED`, `WORKER_WATCHDOG_TIMEOUT`, `WORKER_MAX_WORKERS`, `WORKER_CAPABILITIES` from env vars; exposes `app.state.worker_log_buffer`
- Auto-merge for local deployment mode via `merge_once()` in `worker/runner.py`; records `TASK_AUTO_MERGE_FAILED` event on failure instead of transitioning to `BLOCKED`
- `core/merge.py`: `MergeResult` dataclass and `squash_merge()` helper encapsulating worktree creation, squash merge, Claude-assisted conflict resolution, commit, ref update, and branch cleanup
- Feature lifecycle status (`idea`, `in_clarification`, `defined`, `generated`, `in_progress`, `done`) now included in `GET /api/features` and `GET /api/features/{feature_id}` responses
- Feature status badges in web UI features list (`FeaturesPage`) and feature detail (`FeatureDetailPage`) pages
- `--features` flag on `scripts/board.py` to display feature board grouped by lifecycle status
- `execution_traces` Postgres table stores trace content; `EXECUTION_TRACE_RECORDED` event marks each trace in the event log
- `ExecutionTrace` Pydantic model in `core/models.py`
- `Store.save_trace()` and `Store.get_trace()` protocol methods; implemented in `InMemoryStore` and `PostgresStore`
- `scripts/migrate-traces.py`: one-off migration of existing `.md` trace files into `execution_traces` table
- `FEATURE_IDEA = "idea"` and `FEATURE_IN_CLARIFICATION = "in_clarification"` feature status constants in `core/events.py`

### Changed
- `scripts/merge-task.py` refactored to call `squash_merge()` from `core/merge.py` instead of inline git subprocess calls
- `ClaudeCodeInvoker` now accepts `store: Store` (required); traces saved to DB instead of filesystem
- `InvocationResult.trace_path` replaced with `trace_id: UUID`
- `web/routes/api/executions.py` reads traces via `store.get_trace()` instead of filesystem
- `core/review_collector.py` reads traces via `store.get_trace()` instead of filesystem
- Renamed `FEATURE_DRAFT` to `FEATURE_DEFINED = "defined"` in `core/events.py`; `FeatureManager.get_feature_status()` now returns `"defined"` where it previously returned `"draft"`
- Board tab and Focus tab pipeline strip no longer render the `spec_qa` column/stage; the status remains functional in the backend and API

### Fixed
- QA pipeline `build-spa` step now runs `npm ci` first so worktrees without `node_modules` can build the SPA
- Web UI deploy endpoint now runs deploy hooks (SPA build was being skipped on UI-triggered deploys)
- `test_task_fields` expected set updated to include `merge_commit_sha`; migration `c1d2e3f4a5b6` adds `merge_commit_sha` column to `current_tasks` view

### Added
- scripts/run-review.py: CLI entry point for the Retrospective Insight Reviewer
- PR-based deployment mode: `deployment: {mode: pr, base_branch: develop}` in `ratchet.yaml` causes the deploy endpoint to push the execution branch and open a GitHub PR instead of squash-merging locally
- `DeploymentConfig` dataclass and `load_deployment_config()` in `core/qa_runner.py`; returns `DeploymentConfig(mode="local")` when section is absent
- `TASK_PR_CREATED` event constant in `core/events.py` with payload `{pr_url, pr_number, branch}`
- `poll_pr_merges()` in `worker/runner.py` polls GitHub every 300 seconds and transitions merged PRs from `ready_for_deployment` to `deployed`
- JobDispatcher in orchestrator/dispatcher.py with dispatch_pending, _dispatch_one, and dispatch_loop
- `remote_worker/` package: `ClaudeAuthError`, `verify_claude_auth()`, `RemoteWorkerClient` with WebSocket reconnect loop; `python -m remote_worker --orchestrator URL --capabilities ...` CLI
- `AssignTaskMessage.git_bundle_b64: str` and `ExecutionCompletedMessage.patch: str` fields in `core/remote_protocol.py`
- `websockets>=12.0` runtime dependency
- `get_next_task()` filters tasks by capability match: tasks whose `required_capabilities` is not a subset of the worker's `local_capabilities` are skipped silently
- `--capabilities CAP1,CAP2` CLI flag on `python -m worker` to specify comma-separated local worker capabilities
- `GET /api/tasks/{task_id}/qa` endpoint returns full Q&A history and pending question for a task
- `POST /api/tasks/{task_id}/answer` endpoint records a human answer to a pending question, validated against `WAITING_FOR_INPUT` status; `answered_by` is always `"spa"`
- Post-deploy hooks via `deploy:` section in `ratchet.yaml`; `deploy-task.py` and web deploy route run all steps unconditionally, record `task.deploy_hooks_run` event with full results, and always transition to `deployed`; `--skip-deploy-hooks` CLI flag and matching web checkbox bypass hook execution entirely
- "New Task" link on project detail page pointing to `/projects/{id}/tasks/new`
- Dependency `<select>` in new-task form now excludes tasks in `deployed` or `abandoned` status
- Read-only web UI (`python -m web`) with board (`GET /`), projects (`GET /projects`, `GET /projects/{id}`), and task detail (`GET /tasks/{id}`) pages rendered via Jinja2 templates; `web/board_builder.py` extracts replay helpers from `scripts/board.py` and is reused by both CLI and web routes
- `core/compiler.py` with HLS compilation logic extracted from `scripts/compile-feature.py`: `run_claude`, `build_compile_prompt`, `extract_spec`, `get_task_status`, `is_eligible`, `compile_hls`, `compile_all`
- `compile_once(store)` in `worker/runner.py` that invokes `compile_all` and returns True if any HLS was compiled
- DB migration `d2e4f6a8b1c3` adds `notify_compilation_trigger` PL/pgSQL function and `trg_notify_compilation` trigger firing on `high_level_spec.added` and `task.status_changed` (deployed) events via `ratchet_compilation_trigger` channel
- `worker/listener.py` now LISTENs on both `ratchet_task_status` and `ratchet_compilation_trigger`; yields typed 3-tuples distinguishing compilation triggers from task status events
- Worker startup catchup calls `compile_once` before `run_once`/`run_qa_once`; single-pass mode does the same
- Unit tests in `core/tests/test_compiler.py` covering `extract_spec`, `is_eligible`, and `compile_all`
- `--verbose` / `-v` flag to `scripts/board.py` to display full 36-char task UUIDs instead of truncated 8-char IDs

### Changed
- `WAITING_FOR_INPUT` state now only allows transitions to `IN_PROGRESS` or `ABANDONED`; `BLOCKED` transition removed
- `transition()` now requires `execution_id` in `extra_payload` when targeting `WAITING_FOR_INPUT`
- worker runs in continuous polling loop by default (30 s idle sleep); add `--once` flag for single-pass exit

### Fixed
- SPA no longer requires a page reload to show baseline QA failures; DB migration `d4e5f6a7b8c9` extends the `notify_task_events` trigger to also fire on `task.baseline_qa_failed` and `task.baseline_qa_retry` events
- QA now runs tool steps (pytest, mypy, ruff) against the execution branch worktree instead of the develop branch, so new/deleted test files are correctly reflected in QA output
- `ClaudeCodeInvoker` strips `CLAUDECODE` env var before spawning claude subprocess to prevent "nested session" failures when worker runs inside a Claude Code session
- `current_projects.updated_at` now reflects `MAX(occurred_at)` across all project events instead of being locked to the creation event
- `current_executions` now includes `branch_name` column extracted from `execution.started` payload, matching the `Execution` Pydantic model
