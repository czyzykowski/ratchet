# Spec 05: Project Onboarding and ProjectManager

## Objective

Implement project registration and validation — the mechanism by which a git repository is onboarded into Ratchet as a managed project, with mechanical verification that minimum onboarding requirements are met.

## Success Criteria

- [ ] `core/project_manager.py` implements `ProjectManager` class accepting a `Store` instance
- [ ] `ProjectManager.register_project(name, repo_path)` validates the repo and appends `PROJECT_CREATED` event, returns a `Project` model
- [ ] `ProjectManager.register_project()` raises `OnboardingError` if repo path does not exist
- [ ] `ProjectManager.register_project()` raises `OnboardingError` if repo path is not a git repository
- [ ] `ProjectManager.register_project()` raises `OnboardingError` if `CLAUDE.md` is missing from repo root
- [ ] `ProjectManager.register_project()` raises `OnboardingError` if `INTENT.md` is missing from `docs/` directory
- [ ] `OnboardingError` message clearly states which requirement failed
- [ ] `ProjectManager.get_project(project_id)` returns a `Project` or `None`
- [ ] `ProjectManager.list_projects()` returns all active projects ordered by `created_at` ascending
- [ ] `ProjectManager.archive_project(project_id)` appends `PROJECT_ARCHIVED` event, returns the event
- [ ] `validate_repo(repo_path)` is implemented as a module-level function in `core/project_manager.py`
- [ ] Unit tests in `core/tests/test_project_manager.py` use `InMemoryStore` and `tmp_path` pytest fixture for repo setup
- [ ] Unit tests cover successful registration with valid repo
- [ ] Unit tests cover all four validation failure cases
- [ ] Unit tests cover `get_project()` returning `None` for unknown id
- [ ] Unit tests cover `list_projects()` returning projects ordered by creation time
- [ ] Unit tests cover `archive_project()` — archived project no longer appears in `list_projects()`
- [ ] Unit tests cover registering two projects and listing both
- [ ] `ruff check .` passes with no errors
- [ ] `pytest core/tests/ -v` passes with no errors and no database connection required
- [ ] Commit: `git add -A && git commit -m "spec(05): project onboarding and ProjectManager"`

## Out of Scope

- Do not implement Docker or container validation — not required for v1
- Do not implement task creation — ProjectManager only manages project entities
- Do not modify database schema or migrations
- Do not implement TUI or API
- Do not modify any existing core modules
- Do not implement flake.nix validation
- Only create `core/project_manager.py` and `core/tests/test_project_manager.py`

## Technical Context

- Language: Python 3.12
- Pattern: same as other managers — accepts `Store` instance, never imports concrete store
- Repo validation uses `pathlib.Path` and `subprocess.run()` — no external libraries
- A valid git repository is detected by presence of `.git/` directory at repo root
- Existing files:
  - `core/store.py` — Store protocol, InMemoryStore, PostgresStore
  - `core/events.py` — PROJECT_CREATED, PROJECT_ARCHIVED event constants
  - `core/models.py` — Project Pydantic model
  - `core/execution_manager.py` — reference pattern for module-level functions + manager class

## Onboarding Contract (v1)

A project is valid for registration if all of the following are true:

1. `local_path` exists on the filesystem
2. `local_path/.git/` exists (valid git repository)
3. `local_path/CLAUDE.md` exists
4. `local_path/docs/INTENT.md` exists

No other requirements. Container definition, flake.nix, and toolchain setup are human responsibilities performed before registration.

Note: In v1, `repo_url` and `local_path` will typically be the same value. They are stored separately so that future remote repo support (GitHub URLs, etc.) requires no schema change — `repo_url` becomes the remote URL and `local_path` becomes Ratchet's local clone location.

## EVENT Payloads

### PROJECT_CREATED

```python
{
    "project_id": str(uuid),
    "name": str,
    "repo_url": str,            # canonical repo identifier (remote URL or local path)
    "local_path": str,          # absolute local path where repo is cloned
    "status": "active"
}
```

### PROJECT_ARCHIVED

```python
{
    "project_id": str(uuid),
    "status": "archived"
}
```

## ProjectManager Interface

```python
class OnboardingError(Exception):
    """Raised when a repo fails onboarding validation. Message states which requirement failed."""

class ProjectManager:
    def __init__(self, store: Store) -> None: ...

    async def register_project(
        self,
        name: str,
        repo_url: str,
        local_path: str
    ) -> Project:
        """
        Validate repo and register as a managed project.
        Calls validate_repo(local_path) — raises OnboardingError on failure.
        Appends PROJECT_CREATED event on success.
        Returns Project model.
        """

    async def get_project(
        self,
        project_id: UUID
    ) -> Project | None:
        """
        Return project by id, or None if not found.
        Derived by replaying PROJECT_CREATED events.
        """

    async def list_projects(self) -> list[Project]:
        """
        Return all active (non-archived) projects ordered by created_at ascending.
        Derived by replaying PROJECT_CREATED and PROJECT_ARCHIVED events.
        """

    async def archive_project(
        self,
        project_id: UUID
    ) -> Event:
        """
        Archive a project — it will no longer appear in list_projects().
        Appends PROJECT_ARCHIVED event.
        Returns the appended event.
        """
```

## Module-Level Function

```python
def validate_repo(local_path: str) -> None:
    """
    Validate that local_path meets Ratchet onboarding contract.
    Raises OnboardingError with descriptive message on first failed requirement:
      - "repo path does not exist: <path>"
      - "not a git repository: <path>"
      - "CLAUDE.md not found at repo root: <path>"
      - "docs/INTENT.md not found: <path>"
    Returns None on success.
    """
```

## Test Scenarios to Cover

```
Validation failures (use tmp_path to create minimal repo structure):
- Non-existent path → OnboardingError "repo path does not exist"
- Path exists but no .git/ → OnboardingError "not a git repository"
- Has .git/ but no CLAUDE.md → OnboardingError "CLAUDE.md not found"
- Has .git/ and CLAUDE.md but no docs/INTENT.md → OnboardingError "docs/INTENT.md not found"

Successful registration:
- Valid repo → PROJECT_CREATED event appended
- Valid repo → returns Project with correct name, repo_url, and local_path
- Valid repo → project appears in list_projects()

Multiple projects:
- Register two projects → list_projects() returns both ordered by created_at
- Archive one → list_projects() returns only the active one

get_project():
- Unknown project_id → None
- Known project_id → returns correct Project

archive_project():
- Archived project no longer in list_projects()
- PROJECT_ARCHIVED event appended with correct project_id
```

## Test Setup Pattern

Use `tmp_path` pytest fixture to create minimal valid repo structure:

```python
def make_valid_repo(tmp_path):
    """Create minimal directory structure passing onboarding validation."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Test Project")
    (repo / "docs").mkdir()
    (repo / "docs" / "INTENT.md").write_text("# Intent")
    return str(repo)
```

## Tasks

- [ ] Create `core/project_manager.py` with `OnboardingError`, module-level `validate_repo()`, and `ProjectManager` class
- [ ] Implement `validate_repo(local_path)` checking all four requirements in order, raising `OnboardingError` with specific message on first failure
- [ ] Implement `register_project(name, repo_url, local_path)` — call validate_repo(local_path), generate UUID, append PROJECT_CREATED, return Project
- [ ] Ensure `Project` model in `core/models.py` has both `repo_url` and `local_path` fields — update if it currently has `repo_path`
- [ ] Implement `get_project()` — replay PROJECT_CREATED events, find by project_id in payload
- [ ] Implement `list_projects()` — replay all project events, return active projects ordered by created_at
- [ ] Implement `archive_project()` — append PROJECT_ARCHIVED event, return event
- [ ] Create `core/tests/test_project_manager.py` with `make_valid_repo` helper and all scenarios above
- [ ] Run `pytest core/tests/ -v` and confirm all tests pass with no database connection
- [ ] Run `ruff check .` and fix all linting errors
- [ ] Commit: `git add -A && git commit -m "spec(05): project onboarding and ProjectManager"`

## Assumptions

- Postgres is already running on 127.0.0.1:5432 — do not attempt to start it
- `local_path` is expected to be an absolute path
- `repo_url` and `local_path` are the same value in v1 — both set to the local absolute path
- `.git/` directory presence is sufficient to identify a git repo — no need to run git commands for validation
- A project can be archived but not deleted — events are immutable
- `InMemoryStore` from spec 02 is the correct store for tests
- `core/models.py` may need updating — `Project` model should have `repo_url` and `local_path` fields, replacing any existing `repo_path` field

## Verification Commands

```bash
ruff check .
pytest core/tests/ -v
```

## What Exists After This Spec

```
core/
  project_manager.py    — OnboardingError, validate_repo, ProjectManager
  tests/
    test_state_machine.py     — unchanged
    test_spec_manager.py      — unchanged
    test_execution_manager.py — unchanged
    test_project_manager.py   — full unit test suite, no DB required
```

Project registration and validation are fully implemented and tested. The onboarding contract is mechanical and minimal — git repo, CLAUDE.md, docs/INTENT.md. Container requirements are deferred. Foundation for the execution context contract in spec 06.
