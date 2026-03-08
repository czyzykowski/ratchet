# Spec N: Add mypy type checking to the project

## Objective

Add mypy to the development environment and configure it with `strict = true` for `core/` and `worker/` packages. Annotate all existing code in those packages until `mypy core worker` exits 0, with per-module overrides for third-party dependencies that lack type stubs.

## Success Criteria
- [x] `pkgs.mypy` added to `buildInputs` in `flake.nix`
- [x] `mypy>=1.0` added to `[project.optional-dependencies] dev` in `pyproject.toml`
- [x] `[tool.mypy]` section added to `pyproject.toml` with `strict = true` and `python_version = "3.12"`
- [x] `[[tool.mypy.overrides]]` entries in `pyproject.toml` for `psycopg*`, `psycopg_pool`, `alembic`, `yaml` with `ignore_missing_imports = true`
- [x] `mypy core worker` exits 0 with no errors or warnings
- [x] All existing tests still pass: `pytest core/tests/ worker/tests/ -v`

## Out of Scope

- Running mypy in CI or modifying the QA script
- Checking `scripts/`, `db/`, or `tui/`
- Adding mypy to pre-commit hooks
- Any logic changes — annotations only

## Technical Context

`flake.nix` has `buildInputs` with `pkgs.python312`, `pkgs.python312Packages.pip`, `pkgs.ruff`, `pkgs.postgresql_16`, `pkgs.git` — no mypy. `pyproject.toml` has a `dev` optional-dependencies group with `ruff>=0.4`, `pytest>=8.0`, `pytest-asyncio>=0.23`. Runtime deps include `psycopg[async]>=3.1`, `psycopg-pool>=3.1`, `alembic>=1.13`, `pyyaml>=6.0` — none of these ship bundled stubs, so each needs an `ignore_missing_imports` override. `pydantic>=2.0` ships its own stubs and needs no override.

Source files to annotate:
- `core/__init__.py`, `core/db.py`, `core/store.py`, `core/models.py`, `core/events.py`, `core/state_machine.py`, `core/spec_manager.py`, `core/project_manager.py`, `core/context_assembler.py`, `core/execution_manager.py`, `core/invoker.py`, `core/qa_runner.py`
- `worker/__init__.py`, `worker/__main__.py`, `worker/runner.py`

## Tasks
- [x] Add `pkgs.mypy` to `buildInputs` list in `flake.nix`
- [x] Add `mypy>=1.0` to `[project.optional-dependencies] dev` in `pyproject.toml`
- [x] Add `[tool.mypy]` config block to `pyproject.toml` with `strict = true`, `python_version = "3.12"`, and `[[tool.mypy.overrides]]` sections for `psycopg`, `psycopg_pool`, `alembic`, `yaml` each with `ignore_missing_imports = true`
- [x] Install mypy: `.venv/bin/pip install mypy`
- [x] Run `mypy core worker` and capture all errors
- [x] Add type annotations to all `core/` source modules to resolve mypy errors
- [x] Add type annotations to all `worker/` source modules to resolve mypy errors
- [x] Verify `mypy core worker` exits 0
- [x] Verify `pytest core/tests/ worker/tests/ -v` still passes

## Assumptions

- `pkgs.mypy` is available in nixpkgs 24.11 (it is — mypy is a standard nixpkg)
- pydantic v2 ships its own py.typed marker and stubs; no override needed
- Test files under `core/tests/` and `worker/tests/` are excluded from mypy checking
- All annotation work is purely additive — no logic changes required

## Verification Commands

```bash
# Install mypy in venv
.venv/bin/pip install mypy

# Run type check — must exit 0
.venv/bin/mypy core worker

# Confirm tests still pass
.venv/bin/pytest core/tests/ worker/tests/ -v
```

## What Exists After This Spec

`flake.nix` includes `pkgs.mypy` so mypy is available in `nix develop`. `pyproject.toml` has a complete `[tool.mypy]` configuration with `strict = true`. All source files in `core/` and `worker/` carry full type annotations and pass `mypy core worker` with exit code 0. The QA script or any other tooling can invoke `mypy core worker` and rely on a clean exit code as a quality gate.