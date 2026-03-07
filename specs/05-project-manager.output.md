```
COMPLETED: specs/05-project-manager.md

Tasks completed: 11/11
Verification:
  ruff check .: PASS
  pytest core/tests/ -v: PASS (70 passed, no DB required)

Files created/modified:
- core/models.py (replaced repo_path with repo_url + local_path on Project)
- core/project_manager.py (OnboardingError, validate_repo, ProjectManager)
- core/tests/test_project_manager.py (20 tests, all pass)

Commit: 81215b3
```
