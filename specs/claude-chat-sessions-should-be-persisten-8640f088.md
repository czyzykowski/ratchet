# Spec N: Persistent Chat Sessions

## Objective

Persist spec and feature design chat sessions to the database so they survive server restarts and allow multi-sitting refinement. Each task has at most one spec session; each feature has at most one feature session. Sessions are resumable from the task or feature detail page, with full message history restored in the UI.

## Success Criteria

- [ ] Creating a spec session for a task that already has a session returns the existing session and its full message history
- [ ] Creating a feature session for a feature that already has a session returns the existing session and its full message history
- [ ] After server restart, sending a message to an existing session resumes correctly (Claude subprocess is respawned with history replayed)
- [ ] Full message history is displayed in the UI when reopening a session
- [ ] Each new message exchange is persisted to the database before the response is returned to the client
- [ ] `current_chat_sessions` materialized view is queryable by `context_id`
- [ ] All existing unit tests pass
- [ ] `mypy`, `ruff`, and `pytest` pass cleanly

## Out of Scope

- Deleting or archiving sessions
- Multiple sessions per task or feature
- Session list / browse page
- Authentication or session ownership

## Technical Context

Sessions currently live in `app.state.spec_sessions` and `app.state.feature_sessions` as `dict[str, SpecReplSession]`. `SpecReplSession` holds a `history: list[tuple[str, str]]` field and already has `_replay_history()` which replays that history into a fresh subprocess — this is the resumption mechanism we will reuse.

The event store (`core/store.py`) has `append_event(event)` and `get_events(aggregate_id, aggregate_type)`. We will use `aggregate_type = "chat_session"` with two event types. The materialized view is derived from these events and enables lookup by `context_id`.

Database queries against materialized views are done in `web/queries.py`. Session hydration (loading DB history into a `SpecReplSession`) happens in the API route handlers, not in `core/claude_repl.py`.

The frontend currently deletes the session on unmount — this must stop. The POST endpoint for session creation must become a "get or create" that also returns history.

## Tasks

- [ ] Add event type constants to `core/events.py`: `CHAT_SESSION_CREATED = "chat_session.created"` and `CHAT_SESSION_MESSAGE_ADDED = "chat_session.message_added"`
- [ ] Add `ChatSession` Pydantic model to `core/models.py` with fields: `id: UUID`, `session_type: Literal["spec", "feature"]`, `context_id: UUID`, `context_type: Literal["task", "feature"]`, `created_at: datetime`, `messages: list[tuple[str, str]]`
- [ ] Create Alembic migration `add_chat_session_materialized_view` that creates `current_chat_sessions` view: `SELECT aggregate_id AS id, payload->>'session_type' AS session_type, (payload->>'context_id')::uuid AS context_id, payload->>'context_type' AS context_type, occurred_at AS created_at FROM events WHERE aggregate_type = 'chat_session' AND event_type = 'chat_session.created'`; add index on `context_id`; include `refresh_views()` trigger update
- [ ] Add `get_chat_session_by_context(pool, context_id: UUID) -> ChatSession | None` to `web/queries.py` — queries `current_chat_sessions` to find session, then calls `store.get_events(session_id, "chat_session")` filtered to `chat_session.message_added` to reconstruct `messages` list
- [ ] Rewrite `POST /api/spec-sessions` in `web/routes/api/spec_sessions.py` to: look up existing session via `get_chat_session_by_context(task_id)`, if found hydrate `SpecReplSession` with DB history and add to `app.state.spec_sessions`, return `{"session_id": ..., "messages": [...]}`. If not found, create new session, persist `CHAT_SESSION_CREATED` event with `{session_type: "spec", context_id: task_id, context_type: "task"}`, return `{"session_id": ..., "messages": []}`
- [ ] Rewrite `POST /api/spec-sessions/{session_id}/message` to accumulate the full assistant response during streaming, then persist a `CHAT_SESSION_MESSAGE_ADDED` event with `{user_input, assistant_text}` before sending the `done` SSE frame
- [ ] Remove the `DELETE /api/spec-sessions/{session_id}` cleanup behaviour from the frontend (keep the endpoint for explicit abandonment but do not call it on unmount)
- [ ] Apply the same three changes (get-or-create, persist message, remove unmount delete) to `web/routes/api/feature_sessions.py`
- [ ] Update `web/spa/src/components/CreateSpecChat.tsx`: on session creation response, if `messages` is non-empty restore them into component state before rendering; remove `DELETE` call from unmount cleanup
- [ ] Update `web/spa/src/components/CreateFeatureChat.tsx`: same history restoration logic; remove `DELETE` call from unmount cleanup
- [ ] Add unit tests in `core/tests/` for: `CHAT_SESSION_CREATED` event round-trip, `CHAT_SESSION_MESSAGE_ADDED` event round-trip, `get_chat_session_by_context` returns `None` when no session exists (using `InMemoryStore`)
- [ ] Verify `ratchet.yaml` QA steps already cover `web/tests/` and `core/tests/` — no changes needed if so

## Assumptions

- The full assistant response is always accumulated in memory before the SSE stream closes — this is already the case since `ask()` yields chunks and the route handler collects them
- `refresh_views()` is called by the existing periodic background task — new view will be included automatically
- Feature sessions router is registered in `web/app.py` (the explore agent noted it may be missing — verify and fix during implementation if so)
- `InMemoryStore` is sufficient for unit tests; no DB required for the core logic tests

## Verification Commands

```bash
# Unit tests (no DB required)
.venv/bin/python -m pytest core/tests/ -v

# Apply migration
.venv/bin/alembic -c db/alembic.ini upgrade head

# Integration smoke test
python db/smoke_test.py

# Type check
.venv/bin/mypy core/ worker/ web/

# Lint
.venv/bin/python -m ruff check .

# Build SPA
cd web/spa && npm ci && npm run build

# Manual verification: start server, open task detail, open spec chat,
# send a message, restart server, reopen spec chat — history should be restored
```

## What Exists After This Spec

Chat sessions for spec and feature design are durable database records linked to their task or feature. Every message exchange is stored as an event. Reopening the chat from a task or feature detail page restores the full conversation. The Claude subprocess is transparently respawned from stored history when needed. This provides an audit trail from design conversation to produced artefact.