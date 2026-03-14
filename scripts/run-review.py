"""CLI entry point for the Retrospective Insight Reviewer pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from uuid import UUID


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Retrospective Insight Reviewer pipeline."
    )
    parser.add_argument(
        "--project-id",
        action="append",
        metavar="UUID",
        dest="project_ids",
        help="Project ID to review (can be specified multiple times).",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Load and review all projects.",
    )
    parser.add_argument(
        "--global",
        action="store_true",
        dest="include_global",
        help="Include global (cross-project) analysis.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print suggestions without interactive review or file writes.",
    )
    parser.add_argument(
        "--output-json",
        metavar="PATH",
        default=None,
        help="Write suggestions as JSON to the given path after the session.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    if not args.project_ids and not args.all_projects and not args.include_global:
        print(
            "Error: specify at least one of --project-id, --all-projects, or --global.",
            file=sys.stderr,
        )
        sys.exit(1)

    from core.db import close_pool
    from core.models import ReviewScope
    from core.project_manager import ProjectManager
    from core.review_collector import ReviewDataCollector
    from core.review_engine import ReviewAnalysisError, ReviewEngine
    from core.review_manager import ReviewManager
    from core.review_session import ReviewSession
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        project_ids: list[UUID] = [UUID(pid) for pid in (args.project_ids or [])]

        all_projects = None
        if args.all_projects:
            pm = ProjectManager(store)
            all_projects = await pm.list_projects()
            id_set = set(project_ids) | {p.id for p in all_projects}
            project_ids = list(id_set)

        scope = ReviewScope(
            project_ids=list(set(project_ids)),
            include_global=args.include_global,
        )

        if all_projects is not None:
            projects = all_projects
        else:
            pm = ProjectManager(store)
            all_loaded = await pm.list_projects()
            projects = [p for p in all_loaded if p.id in set(project_ids)]

        review_manager = ReviewManager(store)
        collector = ReviewDataCollector(store)
        engine = ReviewEngine()
        session = ReviewSession(review_manager)

        review_run = await review_manager.start_run(scope)

        print(f"Collecting data across {len(projects)} project(s)...")
        data = await collector.collect(scope, projects)

        print("Analyzing with Claude...")

        previous_run_summary: str
        if review_run.previous_run_id:
            previous_run_summary = await review_manager.get_previous_run_summary(
                review_run.previous_run_id
            )
        else:
            previous_run_summary = ""

        try:
            suggestions = engine.analyze(review_run, data, previous_run_summary)
        except ReviewAnalysisError as exc:
            print(f"Analysis failed: {exc}", file=sys.stderr)
            print(exc.raw_output, file=sys.stderr)
            sys.exit(1)

        for suggestion in suggestions:
            await review_manager.record_suggestion(suggestion)

        print(f"Found {len(suggestions)} suggestion(s).")

        if args.dry_run:
            for i, s in enumerate(suggestions, 1):
                print(session._format_block(s, i, len(suggestions)))
        else:
            suggestions = await session.run(review_run, suggestions)

        await review_manager.complete_run(review_run.id, suggestions)

        if args.output_json:
            with open(args.output_json, "w") as f:
                json.dump([s.model_dump(mode="json") for s in suggestions], f, indent=2)

        applied = sum(1 for s in suggestions if s.status == "applied")
        dismissed = sum(1 for s in suggestions if s.status == "dismissed")
        skipped = sum(1 for s in suggestions if s.status == "skipped")
        print(f"Applied: {applied} | Dismissed: {dismissed} | Skipped: {skipped}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
