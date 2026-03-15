"""ReviewEngine: invokes Claude to analyze collected review data and return Suggestions."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from statistics import mean
from uuid import uuid4

from core.models import ReviewRun, Suggestion, SuggestionEvidence
from core.models_config import WORKER_MODEL
from core.review_collector import CollectedData


def _completion_instructions_path() -> str:
    """Return absolute path to context_assembler.py."""
    return str(Path(__file__).parent / "context_assembler.py")


class ReviewAnalysisError(Exception):
    def __init__(self, message: str, raw_output: str) -> None:
        super().__init__(message)
        self.raw_output = raw_output


class ReviewEngine:
    def analyze(
        self,
        review_run: ReviewRun,
        data: CollectedData,
        previous_run_summary: str,
    ) -> list[Suggestion]:
        prompt = self._build_prompt(data, previous_run_summary)

        base_cmd = ["claude", "-p", prompt, "--model", WORKER_MODEL, "--allowedTools", ""]
        if os.environ.get("USE_NIX_DEVELOP"):
            cmd = ["nix", "develop", "--command"] + base_cmd
        else:
            cmd = base_cmd

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, env=env
        )
        raw = result.stdout

        stripped = raw.strip()
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start == -1 or end == -1:
            raise ReviewAnalysisError("No JSON array found in Claude output", raw)
        json_str = stripped[start : end + 1]
        try:
            items = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ReviewAnalysisError(f"JSON parse error: {exc}", raw) from exc
        if not items:
            raise ReviewAnalysisError("Claude returned empty suggestions array", raw)

        suggestions = []
        for order, item in enumerate(items):
            evidence = SuggestionEvidence(**item.pop("evidence", {}))
            item.pop("status", None)
            suggestion = Suggestion(
                id=uuid4(),
                review_run_id=review_run.id,
                order=order,
                status="pending",
                evidence=evidence,
                **item,
            )
            suggestions.append(suggestion)
        return suggestions

    def _build_prompt(self, data: CollectedData, previous_run_summary: str) -> str:
        lines: list[str] = []

        # 1. Role preamble
        lines.append(
            "You are an expert engineering coach reviewing an "
            "AI-driven autonomous development system."
        )
        lines.append("")

        # 2. Stats block
        total_tasks = len(data.tasks)
        blocked_count = sum(
            1
            for task in data.tasks
            if any(
                e.status == "failed"
                for e in data.executions_by_task.get(task.id, [])
            )
        )
        if data.tasks:
            avg_spec_refinement = round(
                mean(t.refinement_count for t in data.tasks), 1
            )
        else:
            avg_spec_refinement = 0.0

        all_failures: list[str] = []
        for q in data.qa_failures:
            reason = q.get("failure_reason")
            if reason:
                all_failures.append(str(reason))
        for execs in data.executions_by_task.values():
            for e in execs:
                if e.failure_reason:
                    all_failures.append(e.failure_reason)
        freq: dict[str, int] = {}
        for r in all_failures:
            freq[r] = freq.get(r, 0) + 1
        top_5_failures = sorted(freq, key=lambda x: freq[x], reverse=True)[:5]

        lines.append("## Summary Statistics")
        lines.append(f"- Total tasks: {total_tasks}")
        lines.append(f"- Blocked tasks: {blocked_count}")
        lines.append(f"- Avg spec refinement count: {avg_spec_refinement}")
        lines.append("- Top 5 failure reasons:")
        for reason in top_5_failures:
            lines.append(f"  - {reason}")
        lines.append("")

        # 3. Per-project CLAUDE.md
        for project_id, content in data.project_claude_md.items():
            lines.append(f"## Project CLAUDE.md (project_id: {project_id})")
            lines.append(content)
            lines.append("")

        # 4. Global CLAUDE.md
        if data.global_claude_md:
            lines.append("## Global CLAUDE.md")
            lines.append(data.global_claude_md)
            lines.append("")

        # 5. ratchet.yaml
        if data.ratchet_yaml_content is not None:
            lines.append("## ratchet.yaml")
            lines.append(data.ratchet_yaml_content)
            lines.append("")

        # 5b. Completion instructions (from core/context_assembler.py)
        lines.append("## Completion Instructions (injected into every agent execution)")
        lines.append(
            "The following text is appended verbatim to every task prompt sent to Claude Code."
            " Suggestions targeting this content should use"
            ' target="completion_instructions" and'
            f' target_path="{_completion_instructions_path()}".'
        )
        lines.append(data.completion_instructions_content)
        lines.append("")

        # 6. QA failures grouped by type (most recent 20)
        lines.append("## QA Failures (most recent 20)")
        groups: dict[str, list[dict[str, object]]] = {
            "lint": [],
            "typecheck": [],
            "test": [],
            "other": [],
        }
        for q in data.qa_failures:
            reason_str = str(q.get("failure_reason", "")).lower()
            if "lint" in reason_str:
                groups["lint"].append(q)
            elif "typecheck" in reason_str:
                groups["typecheck"].append(q)
            elif "test" in reason_str:
                groups["test"].append(q)
            else:
                groups["other"].append(q)

        all_qa = data.qa_failures[-20:]
        for group_name, group_items in groups.items():
            recent = [q for q in all_qa if q in group_items]
            if recent:
                lines.append(f"### {group_name}")
                for q in recent:
                    tid = q.get("task_id")
                    eid = q.get("execution_id")
                    reason = q.get("failure_reason")
                    lines.append(f"- task={tid} exec={eid}: {reason}")
        lines.append("")

        # 7. Top 10 tasks by refinement count
        lines.append("## Top 10 Tasks by Refinement Count")
        top_tasks = sorted(
            data.tasks, key=lambda t: t.refinement_count, reverse=True
        )[:10]
        for task in top_tasks:
            execs = data.executions_by_task.get(task.id, [])
            failure_chain = [
                e.failure_reason for e in execs if e.failure_reason
            ]
            chain_str = " -> ".join(failure_chain) if failure_chain else "none"
            lines.append(
                f"- {task.title} "
                f"(refinements: {task.refinement_count}): {chain_str}"
            )
        lines.append("")

        # 8. BLOCKED execution traces (up to 5)
        lines.append("## Blocked Execution Traces")
        blocked_execs = [
            e
            for execs in data.executions_by_task.values()
            for e in execs
            if e.status == "failed"
        ][:5]
        for exec_ in blocked_execs:
            trace = data.trace_contents.get(exec_.id, "")
            lines.append(f"### Execution {exec_.id}")
            lines.append(trace[:3000])
            lines.append("")

        # 9. Git log summaries
        lines.append("## Git Log Summaries")
        for project_id, log_text in data.git_log.items():
            lines.append(f"### Project {project_id}")
            last_50 = "\n".join(log_text.splitlines()[-50:])
            lines.append(last_50)
            lines.append("")

        # 10. Previous run summary
        if previous_run_summary:
            lines.append("## Previous Run Summary")
            lines.append(previous_run_summary)
            lines.append("")

        # 11. Output instruction
        lines.append("## Output Instructions")
        lines.append(
            "Output a JSON array (optionally wrapped in a markdown code fence) "
            "where each element has exactly these keys:"
        )
        lines.append(
            '"target" (one of "project_claude_md", "global_claude_md", "ratchet_yaml",'
            ' "completion_instructions"),'
        )
        lines.append('"target_path" (absolute path string),')
        lines.append('"title", "reasoning",')
        lines.append(
            '"evidence" (object with keys: task_ids, execution_ids, '
            "trace_excerpts, failure_reasons, spec_refinement_counts, "
            "git_log_excerpts),"
        )
        lines.append('"confidence" ("low"/"medium"/"high"),')
        lines.append('"priority" ("low"/"medium"/"high"),')
        lines.append('"current_content_excerpt",')
        lines.append('"suggested_diff"')

        return "\n".join(lines)
