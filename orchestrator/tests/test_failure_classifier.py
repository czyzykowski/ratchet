"""Unit tests for QA failure classification."""
from __future__ import annotations

from orchestrator.failure_classifier import classify_qa_failure


class TestClassifyInfra:
    def test_dlopen_failure(self) -> None:
        assert classify_qa_failure("dlopen(...Library not loaded...)") == "infra"

    def test_library_not_loaded(self) -> None:
        assert classify_qa_failure("Library not loaded: /usr/lib/libfoo.dylib") == "infra"

    def test_nix_store_path(self) -> None:
        assert classify_qa_failure("error: path '/nix/store/abc123-foo' is not valid") == "infra"

    def test_disk_full(self) -> None:
        assert classify_qa_failure("write failed: ENOSPC") == "infra"

    def test_permission_denied_system_path(self) -> None:
        assert classify_qa_failure("permission denied: /usr/local/bin/foo") == "infra"

    def test_create_worktree_failure(self) -> None:
        assert classify_qa_failure("fatal: create_worktree failed for /path") == "infra"


class TestClassifyCode:
    def test_pytest_failure(self) -> None:
        assert classify_qa_failure("FAILED test_something") == "code"

    def test_assertion_error(self) -> None:
        assert classify_qa_failure("AssertionError: expected 1 got 2") == "code"

    def test_ruff_lint(self) -> None:
        assert classify_qa_failure("ruff check found 3 errors") == "code"

    def test_mypy_error(self) -> None:
        assert classify_qa_failure("mypy: error: Found 2 errors in 1 file") == "code"

    def test_syntax_error(self) -> None:
        assert classify_qa_failure("SyntaxError: invalid syntax") == "code"


class TestClassifySystem:
    def test_pipeline_crashed(self) -> None:
        assert classify_qa_failure("pipeline crashed") == "system"

    def test_unknown_error(self) -> None:
        assert classify_qa_failure("something unexpected happened") == "system"

    def test_empty_string(self) -> None:
        assert classify_qa_failure("") == "system"
