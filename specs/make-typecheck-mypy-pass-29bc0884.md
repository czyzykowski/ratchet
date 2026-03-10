# Spec N: Make typecheck mypy pass

## Objective

Make `mypy --strict` pass cleanly across `core/` and `worker/` by pointing mypy at the virtualenv's Python interpreter so it can resolve installed packages (pydantic, psycopg, etc.), then fix any genuine type errors that surface once imports resolve.

## Success Criteria
- [ ] `mypy` resolves all third-party imports without `import-not-found` errors
- [ ] `.venv/bin/mypy core/ worker/` reports zero errors
- [ ] No `ignore_missing_imports = true` added for `pydantic` (it ships inline types; the fix is interpreter resolution, not suppression)
- [ ] All fixes are minimal — no unnecessary `# type: ignore` comments

## Out of Scope

- Adding mypy to CI
- Fixing test files (`core/tests/` and `worker/tests/` are already excluded in `pyproject.toml`)
- Changing runtime logic to satisfy the type checker

## Technical Context

`pyproject.toml` has `[tool.mypy]` with `strict = true`, `python_version = "3.12"`, and `exclude = ["core/tests/", "worker/tests/"]`. Overrides with `ignore_missing_imports = true` exist for `psycopg.*`, `psycopg_pool.*`, `alembic.*`, and `yaml`. Pydantic v2 ships its own inline types and does not need `ignore_missing_imports` — mypy just needs visibility into the venv's site-packages. Adding `python_executable = ".venv/bin/python"` to `[tool.mypy]` achieves this. After that, any genuine type errors across `core/` and `worker/` must be resolved.

## Tasks
- [ ] Add `python_executable = ".venv/bin/python"` to `[tool.mypy]` in `pyproject.toml`
- [ ] Run `.venv/bin/mypy core/ worker/` and collect all remaining errors
- [ ] Fix each reported type error in `core/` and `worker/` (annotation gaps, incompatible types, missing return types, etc.)
- [ ] Re-run mypy and confirm zero errors

## Assumptions

- `.venv` exists and all dependencies are installed (`pip install -e ".[dev]"`)
- Pydantic v2 inline types are sufficient; no `pydantic-stubs` package needed
- Existing `ignore_missing_imports` overrides for `psycopg`, `alembic`, `yaml` remain as-is

## Verification Commands
```bash
.venv/bin/mypy core/ worker/
# Expected: Success: no issues found in N source files
```

## What Exists After This Spec

`mypy --strict` passes cleanly across the entire `core/` and `worker/` codebase. The configuration is self-contained in `pyproject.toml` and works for any developer who runs `.venv/bin/mypy` after setting up the venv.