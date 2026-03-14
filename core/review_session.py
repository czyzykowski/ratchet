from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.models import ReviewRun, Suggestion, SuggestionEvidence
from core.review_manager import ReviewManager

SEPARATOR = "═" * 54


class ReviewSession:
    def __init__(self, review_manager: ReviewManager) -> None:
        self._review_manager = review_manager

    def _read_char(self) -> str:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            return ch
        except (AttributeError, Exception):
            return input()[0]

    def _format_block(self, suggestion: Suggestion, index: int, total: int) -> str:
        evidence: SuggestionEvidence = suggestion.evidence

        tasks_str = (
            ", ".join(str(tid) for tid in evidence.task_ids)
            if evidence.task_ids
            else "(none)"
        )
        failures_str = (
            ", ".join(evidence.failure_reasons)
            if evidence.failure_reasons
            else "(none)"
        )
        traces_str = f"{len(evidence.trace_excerpts)} excerpt(s) available"
        specs_str = (
            ", ".join(f"{k}:{v}" for k, v in evidence.spec_refinement_counts.items())
            if evidence.spec_refinement_counts
            else "(none)"
        )

        lines = [
            SEPARATOR,
            f"Suggestion [{index}/{total}]"
            f" — PRIORITY {suggestion.priority}"
            f" — CONFIDENCE {suggestion.confidence}",
            f"Target: {suggestion.target_path}",
            f"Title: {suggestion.title}",
            "",
            "Reasoning:",
            suggestion.reasoning,
            "",
            "Evidence:",
            f"  Tasks: {tasks_str}",
            f"  Failures: {failures_str}",
            f"  Traces: {traces_str}",
            f"  Specs: {specs_str}",
            "",
            "Proposed diff:",
            suggestion.suggested_diff,
            SEPARATOR,
            "[a]pply  [d]ismiss  [s]kip  [v]iew full evidence  [q]uit",
        ]
        return "\n".join(lines)

    def _print_full_evidence(self, suggestion: Suggestion) -> None:
        evidence = suggestion.evidence
        print(f"\n{SEPARATOR}")
        print("Full Evidence")
        print(SEPARATOR)
        print(f"Task IDs: {', '.join(str(t) for t in evidence.task_ids) or '(none)'}")
        print(
            f"Execution IDs: {', '.join(str(e) for e in evidence.execution_ids) or '(none)'}"
        )
        print("\nFailure reasons:")
        for r in evidence.failure_reasons:
            print(f"  - {r}")
        if not evidence.failure_reasons:
            print("  (none)")
        print("\nSpec refinement counts:")
        for k, v in evidence.spec_refinement_counts.items():
            print(f"  {k}: {v}")
        if not evidence.spec_refinement_counts:
            print("  (none)")
        print(f"\nTrace excerpts ({len(evidence.trace_excerpts)}):")
        for i, excerpt in enumerate(evidence.trace_excerpts, 1):
            print(f"\n--- Excerpt {i} ---")
            print(excerpt)
        print(f"\nGit log excerpts ({len(evidence.git_log_excerpts)}):")
        for i, excerpt in enumerate(evidence.git_log_excerpts, 1):
            print(f"\n--- Git log {i} ---")
            print(excerpt)
        print(SEPARATOR)

    def _apply_patch(self, suggestion: Suggestion) -> tuple[bool, str]:
        path = Path(suggestion.target_path)
        if path.parent and str(path.parent) != ".":
            cwd = str(path.parent)
        else:
            cwd = "."

        result = subprocess.run(
            ["patch", "-p0"],
            input=suggestion.suggested_diff,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout
        return True, ""

    async def run(
        self, review_run: ReviewRun, suggestions: list[Suggestion]
    ) -> list[Suggestion]:
        pending = [s for s in suggestions if s.status == "pending"]
        total = len(suggestions)

        for i, suggestion in enumerate(pending, 1):
            while True:
                print(self._format_block(suggestion, i, total))
                ch = self._read_char()

                if ch == "a":
                    success, error = self._apply_patch(suggestion)
                    if not success:
                        print(f"Patch failed: {error}")
                        continue
                    await self._review_manager.apply_suggestion(
                        review_run.id, suggestion.id, suggestion.target_path
                    )
                    suggestion.status = "applied"
                    print("✓ Applied")
                    break

                elif ch == "d":
                    await self._review_manager.dismiss_suggestion(
                        review_run.id, suggestion.id
                    )
                    suggestion.status = "dismissed"
                    print("✗ Dismissed")
                    break

                elif ch == "s":
                    await self._review_manager.skip_suggestion(
                        review_run.id, suggestion.id
                    )
                    suggestion.status = "skipped"
                    print("→ Skipped")
                    break

                elif ch == "v":
                    self._print_full_evidence(suggestion)
                    continue

                elif ch == "q":
                    remaining = pending[i - 1 :]
                    for s in remaining:
                        if s.status == "pending":
                            await self._review_manager.skip_suggestion(
                                review_run.id, s.id
                            )
                            s.status = "skipped"
                    return suggestions

                else:
                    print("Unknown command, try again.")
                    continue

        return suggestions
