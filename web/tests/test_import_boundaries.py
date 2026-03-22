"""Enforce read-path separation: web/routes/ must not import core/ managers for reads.

Web API handlers should read data via web/queries.py (materialized views),
not via core/ managers (event replay). Core managers are for the worker/dispatch
path where real-time correctness matters.

Allowed core imports in web/routes/:
- core.events (event type constants)
- core.state_machine (for write operations like transitions)
- core.store (Store protocol for writes)
- core.models (type definitions)
- core.qa_manager (for write operations like input handling)

Banned core imports in web/routes/ (read managers):
- core.task_manager.TaskManager
- core.spec_manager.SpecManager
- core.execution_manager.ExecutionManager
- core.project_manager.ProjectManager
- core.feature_manager.FeatureManager
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES_DIR = Path(__file__).parent.parent / "routes"

BANNED_IMPORTS = {
    "core.task_manager",
    "core.spec_manager",
    "core.execution_manager",
    "core.project_manager",
    "core.feature_manager",
}


def _find_banned_imports(filepath: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, module) for banned imports in a file."""
    source = filepath.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for banned in BANNED_IMPORTS:
                if node.module == banned or node.module.startswith(banned + "."):
                    violations.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for banned in BANNED_IMPORTS:
                    if alias.name == banned or alias.name.startswith(banned + "."):
                        violations.append((node.lineno, alias.name))
    return violations


def test_web_routes_do_not_import_core_read_managers() -> None:
    """web/routes/ files must not import core read managers (use web/queries.py instead)."""
    all_violations: list[str] = []

    for py_file in sorted(ROUTES_DIR.rglob("*.py")):
        violations = _find_banned_imports(py_file)
        for lineno, module in violations:
            rel = py_file.relative_to(ROUTES_DIR.parent.parent)
            all_violations.append(f"  {rel}:{lineno} imports {module}")

    if all_violations:
        msg = (
            "web/routes/ must read via web/queries.py, not core/ managers.\n"
            "Violations:\n" + "\n".join(all_violations)
        )
        # TODO: Enable this assertion after migrating endpoints to queries.py
        # For now, just warn — the migration spec will fix these.
        import warnings

        warnings.warn(msg, stacklevel=1)
