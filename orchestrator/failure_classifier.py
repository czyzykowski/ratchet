"""Classify QA failures as infrastructure, code, or system errors."""
from __future__ import annotations

import re
from typing import Literal

FailureCategory = Literal["code", "infra", "system"]

# Infrastructure patterns — errors in the environment, not the code.
# Order matters: checked first so infra takes precedence over code.
_INFRA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"dlopen", re.IGNORECASE),
    re.compile(r"Library not loaded", re.IGNORECASE),
    re.compile(r"nix/store", re.IGNORECASE),
    re.compile(r"ENOSPC"),
    re.compile(r"permission denied.*(?:/usr|/nix|/etc|/var|/lib)", re.IGNORECASE),
    re.compile(r"create_worktree", re.IGNORECASE),
]

# Code patterns — errors a fix attempt can plausibly repair.
_CODE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"FAILED\s+\S+"),  # pytest FAILED markers
    re.compile(r"AssertionError|AssertionError", re.IGNORECASE),
    re.compile(r"ruff\s+check|ruff:", re.IGNORECASE),
    re.compile(r"flake8", re.IGNORECASE),
    re.compile(r"mypy", re.IGNORECASE),
    re.compile(r"SyntaxError"),
]


def classify_qa_failure(failure_reason: str) -> FailureCategory:
    """Classify a QA failure reason string.

    Returns:
        "infra" — environment/infrastructure issue, fix attempts are pointless
        "code"  — test/lint/type error, a fix attempt may resolve it
        "system" — unknown or pipeline-level issue
    """
    for pattern in _INFRA_PATTERNS:
        if pattern.search(failure_reason):
            return "infra"

    for pattern in _CODE_PATTERNS:
        if pattern.search(failure_reason):
            return "code"

    return "system"
