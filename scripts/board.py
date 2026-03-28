"""Print the current task board grouped by status across all projects."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from core import events as ev
from web.board_builder import (
    STATUS_LABELS,
    STATUS_ORDER,
    get_task_status,
    load_board,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current task board.")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show full 36-char task UUIDs instead of truncated 8-char IDs.",
    )
    parser.add_argument(
        "--abandoned", action="store_true",
        help="Show only abandoned tasks instead of the default board.",
    )
    parser.add_argument(
        "--merged", action="store_true",
        help="Show only merged tasks instead of the default board.",
    )
    parser.add_argument(
        "--features", action="store_true",
        help="Show feature board grouped by lifecycle status instead of the task board.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.store import PostgresStore

    FEATURE_STATUS_ORDER = [
        ev.FEATURE_IDEA,
        ev.FEATURE_IN_CLARIFICATION,
        ev.FEATURE_DEFINED,
        ev.FEATURE_GENERATED,
        ev.FEATURE_IN_PROGRESS,
        ev.FEATURE_DONE,
    ]
    FEATURE_STATUS_LABELS = {
        ev.FEATURE_IDEA: "IDEA",
        ev.FEATURE_IN_CLARIFICATION: "IN CLARIFICATION",
        ev.FEATURE_DEFINED: "DEFINED",
        ev.FEATURE_GENERATED: "GENERATED",
        ev.FEATURE_IN_PROGRESS: "IN PROGRESS",
        ev.FEATURE_DONE: "DONE",
    }

    store = PostgresStore()
    try:
        if args.features:
            from core.feature_manager import FeatureManager
            from core.project_manager import ProjectManager

            pm = ProjectManager(store)
            fm = FeatureManager(store)

            projects = await pm.list_projects()
            features_by_status: dict[str, list[tuple[str, str, str]]] = {
                s: [] for s in FEATURE_STATUS_ORDER
            }
            for project in projects:
                raw_features = await fm.list_features(project.id)
                for feature in raw_features:
                    status = await fm.get_feature_status(feature.id)
                    if status not in features_by_status:
                        features_by_status[status] = []
                    features_by_status[status].append(
                        (str(feature.id), feature.title, project.name)
                    )

            print("=== RATCHET FEATURE BOARD ===")
            total = sum(len(v) for v in features_by_status.values())
            if total == 0:
                print("\nNo features found.")
                return
            for status in FEATURE_STATUS_ORDER:
                feature_list = features_by_status.get(status, [])
                if not feature_list:
                    continue
                label = FEATURE_STATUS_LABELS.get(status, status.upper())
                print(f"\n{label} ({len(feature_list)})")
                for feat_id, feat_title, proj_name in feature_list:
                    print(f"  [{feat_id[:8]}] {feat_title} — {proj_name}")
            return

        all_tasks, project_by_id, task_events_cache = await load_board(store)

        print("=== RATCHET BOARD ===")

        # Show tasks with pending baseline QA failures (from events, no live re-run)
        from core.event_queries import (  # noqa: PLC0415
            has_pending_baseline_qa_failure as _has_pending_baseline_qa_failure,
        )
        baseline_blocked: list[str] = []
        for task in all_tasks:
            if task["status"] not in (ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT):
                continue
            task_id = task["id"]
            tevents = task_events_cache.get(task_id, [])
            if _has_pending_baseline_qa_failure(tevents):
                project_name = project_by_id.get(task["project_id"])
                proj_label = project_name.name if project_name else "unknown"
                baseline_blocked.append(f"  {proj_label} — {task['title'][:60]}")
        if baseline_blocked:
            print("\n⚠  BASELINE QA BLOCKED (worker skipping these tasks):")
            for line in baseline_blocked:
                print(line)

        if args.abandoned:
            abandoned_tasks = [t for t in all_tasks if t["status"] == ev.ABANDONED]
            if not abandoned_tasks:
                print("\nNo abandoned tasks found.")
                return
            label = STATUS_LABELS.get(ev.ABANDONED, "ABANDONED")
            print(f"\n{label} ({len(abandoned_tasks)})")
            for task in abandoned_tasks:
                task_id_display = str(task["id"]) if args.verbose else str(task["id"])[:8]
                project_name = project_by_id.get(task["project_id"], None)
                project_label = project_name.name if project_name else "unknown"
                count = task["refinement_count"]
                ref_label = f"{count} refinement{'s' if count != 1 else ''}"
                print(f"  [{task_id_display}] {task['title']} — {project_label} — {ref_label}")
            return

        if args.merged:
            merged_tasks = [t for t in all_tasks if t["status"] == ev.DEPLOYED]
            if not merged_tasks:
                print("\nNo merged tasks found.")
                return
            label = STATUS_LABELS.get(ev.DEPLOYED, "MERGED")
            print(f"\n{label} ({len(merged_tasks)})")
            for task in merged_tasks:
                task_id_display = str(task["id"]) if args.verbose else str(task["id"])[:8]
                project_name = project_by_id.get(task["project_id"], None)
                project_label = project_name.name if project_name else "unknown"
                count = task["refinement_count"]
                ref_label = f"{count} refinement{'s' if count != 1 else ''}"
                print(f"  [{task_id_display}] {task['title']} — {project_label} — {ref_label}")
            return

        tasks_by_status: dict[str, list[dict]] = {s: [] for s in STATUS_ORDER}
        for task in all_tasks:
            status = task["status"]
            if status == ev.ABANDONED:
                continue
            if status == ev.DEPLOYED:
                continue
            if status not in tasks_by_status:
                tasks_by_status[status] = []
            tasks_by_status[status].append(task)

        total = sum(len(v) for v in tasks_by_status.values())
        if total == 0:
            print("\nNo tasks found.")
            return

        for status in STATUS_ORDER:
            task_list = tasks_by_status.get(status, [])
            if not task_list:
                continue
            label = STATUS_LABELS.get(status, status.upper())
            print(f"\n{label} ({len(task_list)})")
            for task in task_list:
                task_id_display = str(task["id"]) if args.verbose else str(task["id"])[:8]
                project_name = project_by_id.get(task["project_id"], None)
                project_label = project_name.name if project_name else "unknown"
                count = task["refinement_count"]
                ref_label = f"{count} refinement{'s' if count != 1 else ''}"
                print(f"  [{task_id_display}] {task['title']} — {project_label} — {ref_label}")
                # Annotate with unmet deps
                unmet_deps = []
                for dep_id_str in task.get("depends_on", []):
                    try:
                        dep_id = UUID(dep_id_str)
                    except ValueError:
                        unmet_deps.append(dep_id_str)
                        continue
                    dep_status = get_task_status(dep_id, task_events_cache)
                    if dep_status != ev.DEPLOYED:
                        unmet_deps.append(dep_id_str)
                if unmet_deps:
                    print(f"    [depends on: {', '.join(unmet_deps)}]")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
