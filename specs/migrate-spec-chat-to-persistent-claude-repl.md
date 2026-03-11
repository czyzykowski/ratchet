# Migrate Spec Chat to Persistent Claude Process (stream-json)

## Objective

Replace the current "new subprocess per message" spec chat implementation with a persistent `claude` process per session using `--input-format stream-json`, so conversation history is maintained natively by Claude rather than reconstructed as a growing text prompt on every turn.

## Background / Problem

The current spec chat (`POST /api/tasks/{task_id}/spec/chat`) spawns a fresh `claude -p` process for every user message, baking the entire conversation history into the prompt text via `_build_continuation_spec_prompt()`. This means:
- Each turn cold-starts a new claude process
- History is injected as `## Conversation History\n[json]` — Claude sees it as document context, not real conversation memory
- The prompt grows unboundedly with each turn
- `scripts/create-spec.py` has the same problem

The `--input-format stream-json` / `--output-format stream-json` flags keep a single process alive for multi-turn conversation. Each message is sent as a JSON line; Claude maintains real conversational context. On process death (max-turns, crash), the session respawns and replays history by injecting prior turns as pre-written messages — no context lost.

## Success Criteria

- [ ] `core/claude_repl.py` contains `SpecReplSession` class with async `ask()` generator that yields text chunks and keeps process alive between calls
- [ ] `SpecReplSession.ensure_alive()` respawns process and replays history if process has exited
- [ ] `POST /api/spec-sessions` creates a session, returns `{"session_id": "<uuid>"}`, registers it in `app.state.spec_sessions`
- [ ] `POST /api/spec-sessions/{session_id}/message` with `{"user_input": "..."}` streams SSE events `{"type":"chunk","text":"..."}` then `{"type":"done","spec":"..."|null}`
- [ ] `DELETE /api/spec-sessions/{session_id}` kills the process and removes session from registry
- [ ] SSE `done` event has `spec` set to the extracted spec content when `## SPEC READY` appears in the response, `null` otherwise
- [ ] Frontend `CreateSpecChat.tsx` creates a session on mount, sends messages to session endpoint (not task endpoint), deletes session on unmount/close
- [ ] Frontend no longer sends `history` array on each message (history is server-side)
- [ ] `scripts/create-spec.py` uses `SpecReplSession` (async) instead of `run_claude()` + prompt building
- [ ] Old `POST /api/tasks/{task_id}/spec/chat` endpoint removed from `web/routes/api/tasks.py`
- [ ] Prompt-building functions `_build_continuation_spec_prompt`, `_build_generation_spec_prompt` removed from `tasks.py`
- [ ] `pytest core/tests/ web/tests/ -v` passes
- [ ] `mypy core/ worker/ web/` passes
- [ ] `ruff check .` passes

## Out of Scope

- Do not modify `core/invoker.py` — single-turn task execution stays as-is
- Do not change how `## SPEC READY` detection and spec extraction works
- Do not change any frontend styling or CSS
- Do not modify `POST /api/tasks/{task_id}/spec` (the assign-spec endpoint)
- Do not add authentication to session endpoints
- Do not add persistent session storage (sessions live only in memory, lost on server restart)
- Do not modify `ratchet.yaml` QA pipeline

## Technical Context

- Stack: Python 3.12, FastAPI, asyncio; TypeScript, React
- Pattern to follow: `~/Downloads/claude_repl.py` — read this file in full before starting
- Key mechanism: `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --allowedTools Read,Glob,WebSearch,Bash`
- stdin: newline-delimited JSON messages (`make_user_msg` / `make_assistant_msg` format from `claude_repl.py`)
- stdout: newline-delimited JSON events; look for `type=="stream_event"` → `event.delta.type=="text_delta"` → `delta.text` for chunks; `type=="result"` signals turn completion
- session_id: captured from first non-"default" value in response events; used in subsequent messages

### Key files

- `~/Downloads/claude_repl.py` — reference implementation (sync, terminal REPL); adapt to async
- `core/invoker.py` — shows how current subprocess pattern works (DO NOT modify, just read for context)
- `web/routes/api/tasks.py:378–553` — current spec chat implementation to be replaced
- `web/routes/api/tasks.py:440–480` — prompt-building functions to be removed after migration
- `web/app.py:50–80` — `lifespan()` function where `app.state` is initialised; add `spec_sessions: dict[str, SpecReplSession]` here
- `web/app.py:83–103` — router registration; register the new spec_sessions router here
- `web/routes/api/router.py` — API router aggregator; include new spec_sessions router
- `web/spa/src/components/CreateSpecChat.tsx` — frontend component to update
- `scripts/create-spec.py` — CLI script to update

## Wire Format Reference

### Sending a user message (stdin line)
```json
{"type":"user","message":{"role":"user","content":"<text>"},"session_id":"<id>","parent_tool_use_id":null}
```

### Replaying history (assistant turn, stdin line)
```json
{"type":"assistant","message":{"role":"assistant","content":"<text>"},"session_id":"<id>","parent_tool_use_id":null}
```

### Receiving a text chunk (stdout event)
```json
{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"<chunk>"}},"session_id":"<id>"}
```

### Receiving turn completion (stdout event)
```json
{"type":"result","result":"<full text>","session_id":"<id>"}
```

## Data Examples

### Session create request
```
POST /api/spec-sessions
Content-Type: application/json

{"task_id": "3c57e4b2-6241-4fc6-a28e-f69153739fe5"}
```

### Session create response
```json
{"session_id": "a1b2c3d4-..."}
```

### Message request
```
POST /api/spec-sessions/a1b2c3d4-.../message
Content-Type: application/json

{"user_input": "Add TaskManager to core — extract task creation and querying"}
```

### SSE stream response
```
data: {"type":"chunk","text":"Let me ask you a few "}
data: {"type":"chunk","text":"clarifying questions.\n\n1. Should the TaskManager"}
...
data: {"type":"done","spec":null}
```

### SSE stream response when spec is ready
```
data: {"type":"chunk","text":"## SPEC READY\n# Spec 1: ..."}
...
data: {"type":"done","spec":"# Spec 1: Add TaskManager...\n\n## Objective\n..."}
```

## Tasks

- [ ] **Task 1: Create `core/claude_repl.py`**

  Create `core/claude_repl.py` with `SpecReplSession` dataclass. Port from `~/Downloads/claude_repl.py` (`ClaudeProcess` + `ReplSession` classes) but using `asyncio.create_subprocess_exec` instead of `subprocess.Popen`, and an async generator `ask()` instead of a blocking method.

  The class must:
  - Accept `task_id: str`, `system_prompt: str`, `cwd: str` in `__init__`
  - Maintain `history: list[tuple[str, str]]` (user_text, assistant_text pairs)
  - Maintain `session_id: str = "default"` (updated from first response event)
  - `ensure_alive()` — async, spawns process if dead; if `history` is non-empty, replays it before returning
  - `_spawn()` — launches `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --allowedTools Read,Glob,WebSearch,Bash` with `cwd=self.cwd`, system prompt via `--system-prompt self.system_prompt`, asyncio PIPE for stdin/stdout, DEVNULL for stderr
  - `_send(obj: dict)` — async, writes `json.dumps(obj) + "\n"` to stdin
  - `_replay_history()` — async, sends each (user_text, assistant_text) pair using `make_user_msg` / `make_assistant_msg` wire format
  - `ask(user_input: str) -> AsyncGenerator[str, None]` — async generator that: calls `ensure_alive()`, sends user message, reads stdout line by line, yields text chunks from `stream_event` events, breaks on `result` event, appends `(user_input, assistant_text)` to history
  - `close()` — async, closes stdin and terminates process
  - Module-level `make_user_msg(text, session_id) -> dict` and `make_assistant_msg(text, session_id) -> dict` matching the wire format above

  Verification: `python -c "from core.claude_repl import SpecReplSession; print('ok')"` succeeds

- [ ] **Task 2: Create `web/routes/api/spec_sessions.py`**

  Create `web/routes/api/spec_sessions.py` with a `router = APIRouter(prefix="/spec-sessions")`.

  Three endpoints:

  **`POST /spec-sessions`**
  - Body: `{"task_id": "<uuid>"}`
  - Looks up task from store → gets project → reads `docs/INTENT.md`
  - Builds system prompt using `_build_initial_spec_prompt(intent_md, task_title, task_title)` (import from `web/routes/api/tasks.py`)
  - Creates `SpecReplSession(task_id=str(task_id), system_prompt=system_prompt, cwd=local_path)`
  - Generates `session_id = str(uuid4())`
  - Stores in `request.app.state.spec_sessions[session_id] = session`
  - Returns `JSONResponse({"session_id": session_id})`

  **`POST /spec-sessions/{session_id}/message`**
  - Body: `{"user_input": "<text>"}`
  - Looks up session from `request.app.state.spec_sessions`; 404 if missing
  - Returns `StreamingResponse` with `media_type="text/event-stream"`
  - The async generator: calls `session.ask(user_input)`, yields `data: {"type":"chunk","text":"<chunk>"}\n\n` for each chunk
  - Collects full text; after loop, checks for `## SPEC READY`; extracts spec if found
  - Yields `data: {"type":"done","spec":<spec_content_or_null>}\n\n`

  **`DELETE /spec-sessions/{session_id}`**
  - Looks up session; 404 if missing
  - Calls `await session.close()`
  - Removes from `request.app.state.spec_sessions`
  - Returns `JSONResponse({"ok": True})`

  Verification: `python -c "from web.routes.api.spec_sessions import router; print('ok')"` succeeds

- [ ] **Task 3: Register router and initialise session registry**

  In `web/app.py`:

  3a. Add import: `from web.routes.api import spec_sessions as spec_sessions_router`

  3b. In `lifespan()` (around line 58, after `app.state.sse_clients = []`), add:
  ```python
  app.state.spec_sessions: dict[str, Any] = {}
  ```
  Also add cleanup in the `finally` block (before `await close_pool()`):
  ```python
  for session in list(app.state.spec_sessions.values()):
      await session.close()
  app.state.spec_sessions.clear()
  ```

  3c. After `app.include_router(api_router)` (around line 102), add:
  ```python
  app.include_router(spec_sessions_router.router, prefix="/api")
  ```

  Verification: server starts without error (`uvicorn web.app:app`)

- [ ] **Task 4: Update `web/routes/api/router.py` if needed**

  Check `web/routes/api/router.py` — if spec_sessions needs to be registered there instead of directly in `app.py`, register it. Otherwise skip this task.

- [ ] **Task 5: Update `CreateSpecChat.tsx`**

  In `web/spa/src/components/CreateSpecChat.tsx`:

  5a. Remove the `history` state and all references to it (`useState<Message[]>`).

  5b. Add `sessionId` state: `const [sessionId, setSessionId] = useState<string | null>(null)`

  5c. Change `sendMessage` signature to `sendMessage(userInput: string, isInitial = false)` — remove `history` and `isInitial` parameter from function (isInitial is only used to decide first message behaviour, which is now always just sending the user_input).

  5d. In `useEffect` on mount: call `createSession()` then `sendMessage(taskTitle)`.

  5e. `createSession()`:
  ```typescript
  async function createSession() {
    const res = await fetch('/api/spec-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    })
    const data = await res.json()
    setSessionId(data.session_id)
    return data.session_id
  }
  ```

  5f. `sendMessage(userInput)` — fetch URL changes to `/api/spec-sessions/${sessionId}/message` with body `{"user_input": userInput}`. Remove `history` and `is_initial` from the request body.

  5g. Messages state: keep `messages` for display only. Each completed assistant response appends `{role:'assistant', content: assistantText}` and each user send appends `{role:'user', content: userInput}` to `messages`.

  5h. On close / unmount: call `DELETE /api/spec-sessions/${sessionId}` if sessionId is set. Add `useEffect` cleanup:
  ```typescript
  useEffect(() => {
    return () => {
      if (sessionIdRef.current) {
        fetch(`/api/spec-sessions/${sessionIdRef.current}`, { method: 'DELETE' })
      }
    }
  }, [])
  ```
  Use a `sessionIdRef = useRef<string | null>(null)` that is kept in sync with `sessionId` state for the cleanup effect.

  5i. Build: `npm --prefix web/spa run build` must succeed with no TypeScript errors.

- [ ] **Task 6: Update `scripts/create-spec.py`**

  Replace the `run_claude()` function and prompt-building loop with `SpecReplSession`.

  6a. Remove: `run_claude()`, `build_continuation_prompt()`, `build_generation_prompt()`, `build_initial_prompt()`.

  6b. Import `SpecReplSession` from `core.claude_repl`.

  6c. In `main()`, after loading `intent_md` and `task_title`, build the system prompt the same way:
  ```python
  from web.routes.api.tasks import _build_initial_spec_prompt
  system_prompt = _build_initial_spec_prompt(intent_md, task_title, task_title)
  session = SpecReplSession(task_id=str(task_id), system_prompt=system_prompt, cwd=local_path)
  ```

  6d. Replace the first `run_claude(initial_prompt, local_path)` call with:
  ```python
  print("\n--- Claude ---")
  output = ""
  async for chunk in session.ask(task_title):
      print(chunk, end="", flush=True)
      output += chunk
  print()
  ```

  6e. Replace subsequent turn loop:
  ```python
  while True:
      user_input = input("\n> ").strip()
      if not user_input:
          continue
      print("\n--- Claude ---")
      output = ""
      async for chunk in session.ask(user_input):
          print(chunk, end="", flush=True)
          output += chunk
      print()
      if await _handle_spec_ready(output, task_id, task_title, store):
          break
  ```
  Remove the `is_trigger` / `build_generation_prompt` logic — the REPL maintains conversation context, so the user just types "generate" and Claude will handle it naturally.

  6f. In `finally` block, call `await session.close()` before `await close_pool()`.

  Verification: `python scripts/create-spec.py --help` succeeds

- [ ] **Task 7: Remove old spec chat endpoint from `tasks.py`**

  In `web/routes/api/tasks.py`:

  7a. Remove `SpecChatBody` class (lines ~483–486)
  7b. Remove `spec_chat` endpoint (lines ~489–553)
  7c. Remove `_build_continuation_spec_prompt` function (lines ~448–459)
  7d. Remove `_build_generation_spec_prompt` function (lines ~462–480)
  7e. Keep `_build_initial_spec_prompt` and `_SPEC_ROLE_PROMPT` — they are still used by `spec_sessions.py`
  7f. Remove `_SPEC_ROLE_PROMPT` only if you move it to a shared location; otherwise keep it in `tasks.py` and import from there in `spec_sessions.py`

  Verification: `ruff check .` passes; `mypy core/ worker/ web/` passes

- [ ] **Task 8: Run full QA**

  ```bash
  .venv/bin/python -m pytest core/tests/ web/tests/ -v
  mypy core/ worker/ web/
  ruff check .
  npm --prefix web/spa run build
  npm --prefix web/spa run typecheck
  ```

  All must pass. Fix any issues before committing.

- [ ] **Task 9: Commit**

  ```bash
  git add -A
  git commit -m "feat: migrate spec chat to persistent claude process with stream-json"
  git log --oneline -3
  ```

## Assumptions

- `claude` binary is on PATH in the environment where the web server runs (same assumption as today)
- `--input-format stream-json` and `--output-format stream-json` flags are available in the installed `claude` CLI version (verify with `claude --help | grep stream`)
- Sessions are in-memory only; a server restart loses active sessions (acceptable — user re-opens the chat)
- Concurrent spec sessions for the same task are allowed (each gets its own process)
- The `--system-prompt` flag accepts multi-line strings including the full INTENT.md content

## Verification Commands

```bash
# Python checks
.venv/bin/python -m pytest core/tests/ web/tests/ -v
mypy core/ worker/ web/
ruff check .

# Frontend checks
npm --prefix web/spa run build
npm --prefix web/spa run typecheck

# Smoke test: import new module
.venv/bin/python -c "from core.claude_repl import SpecReplSession; print('ok')"

# Smoke test: claude supports stream-json (must show stream-json in output)
claude --help 2>&1 | grep -i stream
```

## What Exists After This Spec

- `core/claude_repl.py` — async `SpecReplSession` class, reusable for any multi-turn claude conversation
- `web/routes/api/spec_sessions.py` — REST endpoints for session lifecycle
- `web/app.py` — registers spec_sessions router, initialises and cleans up `app.state.spec_sessions`
- `web/spa/src/components/CreateSpecChat.tsx` — uses session-based API, no client-side history management
- `scripts/create-spec.py` — uses `SpecReplSession` directly, no prompt-building machinery
- `web/routes/api/tasks.py` — spec chat removed; only `_build_initial_spec_prompt` and `_SPEC_ROLE_PROMPT` remain for use by spec_sessions.py
