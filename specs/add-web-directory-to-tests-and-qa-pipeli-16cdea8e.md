# Spec 1: Add web directory to tests and QA pipeline

## Objective

Include `web/` in the ratchet QA pipeline (tests and type checking) and create a minimal test suite for `web/app.py` using `InMemoryStore` and FastAPI's `TestClient`. Update `CLAUDE.md` to document the requirement to register new modules in the QA pipeline.

## Success Criteria
- [ ] `ratchet.yaml` test step runs `pytest core/tests/ web/tests/ -v`
- [ ] `ratchet.yaml` typecheck step runs `mypy core/ worker/ web/`
- [ ] `pyproject.toml` mypy `exclude` list no longer contains `"web/"`
- [ ] `web/tests/__init__.py` exists
- [ ] `web/tests/test_app.py` exists with tests for all 5 routes (`/`, `/board`, `/projects`, `/blocked`, `/features`)
- [ ] Each route test asserts HTTP 200 and does not require a real database
- [ ] `pytest core/tests/ web/tests/ -v` passes with no errors
- [ ] `mypy core/ worker/ web/` passes with no errors
- [ ] `CLAUDE.md` contains a note that new top-level module directories must be added to the `test` and `typecheck` steps in `ratchet.yaml`

## Out of Scope

- Adding API endpoints to `web/app.py`
- Integration tests against a real database
- Template rendering assertions (only status codes required)

## Technical Context

- `web/app.py` imports `core.db` lazily inside the `lifespan` context manager — tests must prevent that lifespan from running real DB code
- FastAPI's `TestClient` (from `starlette.testclient`) accepts a `raise_server_exceptions=True` flag and supports overriding the lifespan via `with TestClient(app) as client`
- The cleanest test approach: use `unittest.mock.patch` to mock `core.db.get_pool` and `core.db.close_pool` before importing the app, or override the lifespan entirely with a no-op using `app.router.lifespan_context`
- `web/static/style.css` and `web/templates/` exist — `StaticFiles` mount uses path `"web/static"` which is relative to CWD; tests should be run from repo root
- `pyproject.toml` line 47: `exclude = ["core/tests/", "worker/tests/", "web/"]` — remove `"web/"` from this list
- `httpx` is a transitive dependency of FastAPI's test utilities; `starlette` is a direct FastAPI dependency — no new test dependencies needed

## Tasks
- [ ] Edit `ratchet.yaml`: change test step to `".venv/bin/python -m pytest core/tests/ web/tests/ -v"` and typecheck step to `"mypy core/ worker/ web/"`
- [ ] Edit `pyproject.toml`: remove `"web/"` from the mypy `exclude` list (line 47)
- [ ] Create `web/tests/__init__.py` (empty)
- [ ] Create `web/tests/test_app.py` with a `TestClient` fixture that replaces the lifespan with a no-op, and 5 tests (one per route) each asserting `response.status_code == 200`
- [ ] Verify `pytest core/tests/ web/tests/ -v` passes
- [ ] Verify `mypy core/ worker/ web/` passes
- [ ] Edit `CLAUDE.md`: add a "QA Pipeline" section under "Running Things" stating that any new top-level module directory must have its `tests/` added to both the `test` and `typecheck` steps in `ratchet.yaml`

## Assumptions

- Tests are run from the repo root so relative paths (`web/templates`, `web/static`) resolve correctly
- `httpx` is already available transitively via FastAPI — no new entries in `pyproject.toml` dependencies needed
- The lifespan no-op override is sufficient; route handlers themselves don't touch the DB

## Verification Commands
```bash
.venv/bin/python -m pytest core/tests/ web/tests/ -v
mypy core/ worker/ web/
ruff check .
```

## What Exists After This Spec

- `web/tests/__init__.py` — empty init file
- `web/tests/test_app.py` — minimal smoke tests for all 5 HTML routes
- `ratchet.yaml` — updated to include `web/tests/` in test step and `web/` in typecheck step
- `pyproject.toml` — mypy no longer excludes `web/`
- `CLAUDE.md` — documents the rule that new modules must be registered in `ratchet.yaml` QA steps