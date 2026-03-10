"""QA runner: loads ratchet.yaml config, runs tool checks, and assembles Claude review prompt."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class QaStep:
    name: str
    command: str


@dataclass
class QaConfig:
    steps: list[QaStep]
    max_fix_attempts: int = 3


@dataclass
class QaStepResult:
    step_name: str
    command: str
    returncode: int
    output: str  # stdout + stderr combined


@dataclass
class QaReviewResult:
    verdict: str  # "passed" | "failed"
    full_output: str


def load_qa_config(local_path: str) -> QaConfig | None:
    """Read <local_path>/ratchet.yaml and return QaConfig, or None if absent/missing qa section.

    Normalizes both plain string and {command: ...} step forms.
    """
    config_path = Path(local_path) / "ratchet.yaml"
    if not config_path.exists():
        return None

    with config_path.open() as f:
        data: Any = yaml.safe_load(f)

    if not isinstance(data, dict) or "qa" not in data:
        return None

    qa_section = data["qa"]
    if not isinstance(qa_section, dict):
        return None

    raw_steps = qa_section.get("steps", {})
    max_fix_attempts = int(qa_section.get("max_fix_attempts", 3))

    steps: list[QaStep] = []
    if isinstance(raw_steps, dict):
        for name, value in raw_steps.items():
            if isinstance(value, str):
                command = value
            elif isinstance(value, dict):
                command = value["command"]
            else:
                continue
            steps.append(QaStep(name=name, command=command))
    elif isinstance(raw_steps, list):
        for item in raw_steps:
            if isinstance(item, dict):
                name = item.get("name", "")
                command = item.get("command", "")
                steps.append(QaStep(name=name, command=command))

    return QaConfig(steps=steps, max_fix_attempts=max_fix_attempts)


def run_qa_steps(config: QaConfig, cwd: str) -> list[QaStepResult]:
    """Run each QA step via subprocess, stop at first failure.

    Returns list of QaStepResult; list may be partial if a step fails.
    """
    results: list[QaStepResult] = []
    for step in config.steps:
        proc = subprocess.run(
            step.command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        output = proc.stdout + proc.stderr
        result = QaStepResult(
            step_name=step.name,
            command=step.command,
            returncode=proc.returncode,
            output=output,
        )
        results.append(result)
        if proc.returncode != 0:
            break
    return results


def check_baseline_qa(project_path: str) -> list[QaStepResult]:
    """Run QA steps on the base branch to detect pre-existing failures.

    Runs steps in project_path directly (no worktree). Returns list of
    failed QaStepResult objects; empty list means all passed or no QA config.
    """
    config = load_qa_config(project_path)
    if config is None:
        return []
    results = run_qa_steps(config, project_path)
    return [r for r in results if r.returncode != 0]


def get_git_diff(cwd: str, execution_branch: str | None = None, base_ref: str = "develop") -> str:
    """Get git diff showing changes made by an execution.

    If execution_branch is provided, diffs base_ref..execution_branch to show
    exactly what the execution agent committed relative to the base branch.
    Falls back to HEAD~1..HEAD when no branch is given (legacy behaviour).
    """
    if execution_branch:
        ref_range = f"{base_ref}..{execution_branch}"
    else:
        ref_range = "HEAD~1..HEAD"
    proc = subprocess.run(
        ["git", "diff", ref_range],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def build_review_prompt(
    spec_content: str, diff: str, step_results: list[QaStepResult]
) -> str:
    """Assemble a Claude review prompt from spec, git diff, and QA step results.

    Instructs Claude to output QA_PASSED: or QA_FAILED: marker.
    """
    steps_summary = "\n\n".join(
        f"### Step: {r.step_name}\nCommand: `{r.command}`\n"
        f"Return code: {r.returncode}\n```\n{r.output}\n```"
        for r in step_results
    )

    return f"""\
You are performing a QA review of a completed implementation task.

## Spec
{spec_content}

## Git diff (HEAD~1..HEAD)
```diff
{diff}
```

## QA Tool Results
{steps_summary}

## Instructions

Review whether the implementation satisfies the spec's success criteria,
given the tool results and diff above.

- If the implementation is correct and all success criteria are met, output exactly:
  QA_PASSED: <brief rationale>

- If there are any issues, output exactly:
  QA_FAILED: <full diagnosis including file paths, line numbers, and suggested fixes>

Your response MUST start with either QA_PASSED: or QA_FAILED: on its own line.
"""


def parse_review_output(output: str) -> QaReviewResult:
    """Scan output for QA_PASSED: or QA_FAILED: marker.

    Captures all text from marker to end of output as full_output.
    If no marker found, treats as failed.
    """
    for line_no, line in enumerate(output.splitlines()):
        if "QA_PASSED:" in line:
            idx = output.index("QA_PASSED:")
            return QaReviewResult(verdict="passed", full_output=output[idx:].strip())
        if "QA_FAILED:" in line:
            idx = output.index("QA_FAILED:")
            return QaReviewResult(verdict="failed", full_output=output[idx:].strip())

    fallback = output.strip() or "No QA marker found in output"
    return QaReviewResult(verdict="failed", full_output=fallback)
