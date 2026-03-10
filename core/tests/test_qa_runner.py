"""Unit tests for core/qa_runner.py."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.qa_runner import (
    QaConfig,
    QaStep,
    check_baseline_qa,
    load_qa_config,
    parse_review_output,
    run_qa_steps,
)

# ---------------------------------------------------------------------------
# load_qa_config
# ---------------------------------------------------------------------------


def test_load_qa_config_with_valid_yaml_plain_string_steps(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          max_fix_attempts: 3
          steps:
            test: "pytest core/tests/ -v"
            lint: "ruff check ."
        """)
    )
    config = load_qa_config(str(tmp_path))
    assert config is not None
    assert config.max_fix_attempts == 3
    assert len(config.steps) == 2
    assert config.steps[0] == QaStep(name="test", command="pytest core/tests/ -v")
    assert config.steps[1] == QaStep(name="lint", command="ruff check .")


def test_load_qa_config_with_object_form_step(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          max_fix_attempts: 2
          steps:
            test:
              command: "pytest -v"
        """)
    )
    config = load_qa_config(str(tmp_path))
    assert config is not None
    assert config.steps[0] == QaStep(name="test", command="pytest -v")


def test_load_qa_config_missing_file_returns_none(tmp_path: Path) -> None:
    config = load_qa_config(str(tmp_path))
    assert config is None


def test_load_qa_config_missing_qa_section_returns_none(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text("other:\n  key: value\n")
    config = load_qa_config(str(tmp_path))
    assert config is None


def test_load_qa_config_empty_file_returns_none(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text("")
    config = load_qa_config(str(tmp_path))
    assert config is None


def test_load_qa_config_defaults_max_fix_attempts(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          steps:
            test: "pytest"
        """)
    )
    config = load_qa_config(str(tmp_path))
    assert config is not None
    assert config.max_fix_attempts == 3


# ---------------------------------------------------------------------------
# parse_review_output
# ---------------------------------------------------------------------------


def test_parse_review_output_passed() -> None:
    output = "QA_PASSED: All tests pass and spec criteria met."
    result = parse_review_output(output)
    assert result.verdict == "passed"
    assert "QA_PASSED:" in result.full_output
    assert "All tests pass" in result.full_output


def test_parse_review_output_failed_multiline() -> None:
    output = textwrap.dedent("""\
    Some preamble text.
    QA_FAILED: Implementation missing feature X.
    - core/foo.py:42: function not implemented
    - Suggested fix: add the missing function
    """)
    result = parse_review_output(output)
    assert result.verdict == "failed"
    assert "QA_FAILED:" in result.full_output
    assert "core/foo.py:42" in result.full_output
    assert "Suggested fix" in result.full_output


def test_parse_review_output_no_marker_treated_as_failed() -> None:
    output = "The implementation looks fine but I forgot to add a marker."
    result = parse_review_output(output)
    assert result.verdict == "failed"


def test_parse_review_output_empty_string() -> None:
    result = parse_review_output("")
    assert result.verdict == "failed"


# ---------------------------------------------------------------------------
# run_qa_steps
# ---------------------------------------------------------------------------


def _make_proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_run_qa_steps_all_pass() -> None:
    config = QaConfig(
        steps=[
            QaStep(name="test", command="pytest"),
            QaStep(name="lint", command="ruff check ."),
        ]
    )
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _make_proc(0, stdout="passed"),
            _make_proc(0, stdout="no issues"),
        ]
        results = run_qa_steps(config, "/fake/path")

    assert len(results) == 2
    assert results[0].returncode == 0
    assert results[1].returncode == 0


def test_run_qa_steps_first_step_fails_stops_early() -> None:
    config = QaConfig(
        steps=[
            QaStep(name="test", command="pytest"),
            QaStep(name="lint", command="ruff check ."),
        ]
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(1, stdout="FAILED", stderr="error")
        results = run_qa_steps(config, "/fake/path")

    assert len(results) == 1
    assert results[0].step_name == "test"
    assert results[0].returncode == 1
    assert "FAILED" in results[0].output
    # subprocess.run called only once (stopped after failure)
    mock_run.assert_called_once()


def test_run_qa_steps_last_step_fails() -> None:
    config = QaConfig(
        steps=[
            QaStep(name="test", command="pytest"),
            QaStep(name="lint", command="ruff check ."),
            QaStep(name="typecheck", command="mypy ."),
        ]
    )
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _make_proc(0, stdout="passed"),
            _make_proc(0, stdout="no issues"),
            _make_proc(1, stderr="type error in foo.py:10"),
        ]
        results = run_qa_steps(config, "/fake/path")

    assert len(results) == 3
    assert results[0].returncode == 0
    assert results[1].returncode == 0
    assert results[2].returncode == 1
    assert "type error in foo.py:10" in results[2].output


# ---------------------------------------------------------------------------
# check_baseline_qa
# ---------------------------------------------------------------------------


def test_check_baseline_qa_no_ratchet_yaml_returns_empty(tmp_path: Path) -> None:
    result = check_baseline_qa(str(tmp_path))
    assert result == []


def test_check_baseline_qa_all_steps_pass_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          steps:
            test: "pytest"
            lint: "ruff check ."
        """)
    )
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _make_proc(0, stdout="passed"),
            _make_proc(0, stdout="no issues"),
        ]
        result = check_baseline_qa(str(tmp_path))

    assert result == []


def test_check_baseline_qa_first_step_fails_returns_failed_steps(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          steps:
            lint: "ruff check ."
            test: "pytest"
        """)
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(1, stdout="E501 line too long")
        result = check_baseline_qa(str(tmp_path))

    assert len(result) == 1
    assert result[0].step_name == "lint"
    assert result[0].returncode == 1
    assert "E501" in result[0].output


def test_check_baseline_qa_second_step_fails_returns_that_step(tmp_path: Path) -> None:
    (tmp_path / "ratchet.yaml").write_text(
        textwrap.dedent("""\
        qa:
          steps:
            test: "pytest"
            lint: "ruff check ."
        """)
    )
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _make_proc(0, stdout="passed"),
            _make_proc(1, stderr="E501 line too long"),
        ]
        result = check_baseline_qa(str(tmp_path))

    assert len(result) == 1
    assert result[0].step_name == "lint"
    assert result[0].returncode == 1


def test_run_qa_steps_combines_stdout_and_stderr() -> None:
    config = QaConfig(steps=[QaStep(name="test", command="pytest")])
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(0, stdout="stdout line", stderr="stderr line")
        results = run_qa_steps(config, "/fake/path")

    assert "stdout line" in results[0].output
    assert "stderr line" in results[0].output
