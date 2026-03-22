"""QA runner: loads ratchet.yaml config, runs tool checks, and assembles Claude review prompt."""

from __future__ import annotations

import dataclasses
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _run_command(command: str, cwd: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command in cwd, wrapped in nix develop if flake.nix is present."""
    if (Path(cwd) / "flake.nix").exists():
        return subprocess.run(
            ["nix", "develop", "--command", "bash", "-c", command],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@dataclass
class QaStep:
    name: str
    command: str


@dataclass
class QaConfig:
    steps: list[QaStep]
    max_fix_attempts: int = 3
    auto_fix: list[str] = dataclasses.field(default_factory=list)


@dataclass
class DeploymentConfig:
    mode: str  # "local" | "pr"
    base_branch: str = "develop"


def load_deployment_config(
    local_path: str, ratchet_yaml: str | None = None
) -> DeploymentConfig:
    """Read <local_path>/ratchet.yaml and return DeploymentConfig.

    Returns DeploymentConfig(mode="local", base_branch="develop") when section is absent.
    When ratchet_yaml is not None, parses that string directly instead of reading from disk.
    """
    if ratchet_yaml is not None:
        data: Any = yaml.safe_load(ratchet_yaml)
    else:
        config_path = Path(local_path) / "ratchet.yaml"
        if not config_path.exists():
            return DeploymentConfig(mode="local", base_branch="develop")
        with config_path.open() as f:
            data = yaml.safe_load(f)

    if not isinstance(data, dict) or "deployment" not in data:
        return DeploymentConfig(mode="local", base_branch="develop")

    deployment_section = data["deployment"]
    if not isinstance(deployment_section, dict):
        return DeploymentConfig(mode="local", base_branch="develop")

    mode = str(deployment_section.get("mode", "local"))
    base_branch = str(deployment_section.get("base_branch", "develop"))
    return DeploymentConfig(mode=mode, base_branch=base_branch)


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


def load_qa_config(local_path: str, ratchet_yaml: str | None = None) -> QaConfig | None:
    """Read <local_path>/ratchet.yaml and return QaConfig, or None if absent/missing qa section.

    When ratchet_yaml is not None, parses that string directly instead of reading from disk.
    Normalizes both plain string and {command: ...} step forms.
    """
    if ratchet_yaml is not None:
        data: Any = yaml.safe_load(ratchet_yaml)
    else:
        config_path = Path(local_path) / "ratchet.yaml"
        if not config_path.exists():
            return None

        with config_path.open() as f:
            data = yaml.safe_load(f)

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

    raw_auto_fix = qa_section.get("auto_fix", [])
    auto_fix: list[str] = [str(cmd) for cmd in raw_auto_fix if cmd]

    return QaConfig(steps=steps, max_fix_attempts=max_fix_attempts, auto_fix=auto_fix)


def load_qa_config_from_string(yaml_content: str) -> QaConfig | None:
    """Parse YAML string directly and return QaConfig, or None if absent/invalid.

    Used by QA sequencer when it receives ratchet.yaml content from ReadFile.
    Returns None for empty or invalid YAML that lacks a qa section.
    """
    try:
        return load_qa_config("", ratchet_yaml=yaml_content)
    except yaml.YAMLError:
        return None


def run_auto_fixes(config: QaConfig, cwd: str) -> bool:
    """Run auto-fix commands from config.auto_fix, commit any changes.

    Runs each command in cwd (wrapped in nix develop if flake.nix present).
    After all commands, commits any modified files with a standard message.
    Returns True if any files were changed and committed, False otherwise.
    """
    if not config.auto_fix:
        return False

    for command in config.auto_fix:
        _run_command(command, cwd)

    # Check if any files were modified
    result = subprocess.run(
        ["git", "diff", "--quiet"],
        cwd=cwd,
    )
    if result.returncode == 0:
        return False  # nothing changed

    subprocess.run(
        ["git", "commit", "-am", "fix: auto-fix lint/format issues"],
        cwd=cwd,
        capture_output=True,
    )
    return True


def run_qa_steps(config: QaConfig, cwd: str) -> list[QaStepResult]:
    """Run each QA step via subprocess, stop at first failure.

    Returns list of QaStepResult; list may be partial if a step fails.
    """
    results: list[QaStepResult] = []
    for step in config.steps:
        proc = _run_command(step.command, cwd)
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


def load_merge_config(local_path: str) -> QaConfig | None:
    """Read <local_path>/ratchet.yaml and return QaConfig for merge section, or None if absent.

    Normalizes both plain string and {command: ...} step forms.
    Ignores max_fix_attempts if present (not applicable to merge hooks).
    """
    config_path = Path(local_path) / "ratchet.yaml"
    if not config_path.exists():
        return None

    with config_path.open() as f:
        data: Any = yaml.safe_load(f)

    if not isinstance(data, dict) or "merge" not in data:
        return None

    deploy_section = data["merge"]
    if not isinstance(deploy_section, dict):
        return None

    raw_steps = deploy_section.get("steps", {})

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

    return QaConfig(steps=steps)


def run_merge_steps(config: QaConfig, cwd: str) -> list[QaStepResult]:
    """Run all merge steps via subprocess, never stopping early on failure.

    Returns list of QaStepResult for every step regardless of return code.
    """
    results: list[QaStepResult] = []
    for step in config.steps:
        proc = _run_command(step.command, cwd)
        output = proc.stdout + proc.stderr
        results.append(
            QaStepResult(
                step_name=step.name,
                command=step.command,
                returncode=proc.returncode,
                output=output,
            )
        )
    return results


def check_baseline_qa(
    project_path: str, ratchet_yaml: str | None = None
) -> list[QaStepResult]:
    """Run QA steps on the base branch to detect pre-existing failures.

    Runs steps in project_path directly (no worktree). Returns list of
    failed QaStepResult objects; empty list means all passed or no QA config.
    When ratchet_yaml is not None, parses it directly instead of reading from disk.
    """
    config = load_qa_config(project_path, ratchet_yaml)
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
        [
            "git", "diff", ref_range,
            "--",
            ".",
            ":!web/spa/dist",
            ":!web/spa/node_modules",
            ":!**/node_modules",
            ":!**/*.min.js",
            ":!**/*.min.css",
        ],
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
