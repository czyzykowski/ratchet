"""Batch compile eligible high-level specs into tasks."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path
from uuid import UUID

from core.compiler import compile_hls, is_eligible


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile eligible high-level specs into tasks for a feature."
    )
    parser.add_argument("--feature-id", required=True, help="Feature UUID")
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    debug = bool(os.environ.get("RATCHET_DEBUG"))

    try:
        feature_id = UUID(args.feature_id)
    except ValueError:
        print(f"Error: invalid feature-id: {args.feature_id!r}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("claude"):
        print("Error: claude binary not found in PATH.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.feature_manager import FeatureManager
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        fm = FeatureManager(store)

        feature = await fm.get_feature(feature_id)
        if feature is None:
            print(f"Error: feature {feature_id} not found.", file=sys.stderr)
            sys.exit(1)

        pm = ProjectManager(store)
        project = await pm.get_project(feature.project_id)
        if project is None:
            print(f"Error: project {feature.project_id} not found.", file=sys.stderr)
            sys.exit(1)

        local_path = project.local_path
        intent_md_path = Path(local_path) / "docs" / "INTENT.md"
        if not intent_md_path.exists():
            print(f"Error: INTENT.md not found at {intent_md_path}", file=sys.stderr)
            sys.exit(1)

        intent_md = intent_md_path.read_text()

        print(f"Feature: {feature.title}")
        print(f"Project: {project.name} ({local_path})")

        specs = await fm.get_high_level_specs(feature_id)
        if not specs:
            print("No high-level specs found for this feature.")
            sys.exit(0)

        eligible = []
        for spec in specs:
            if await is_eligible(spec, fm, store):
                eligible.append(spec)

        print(f"\nTotal high-level specs: {len(specs)}")
        print(f"Eligible for compilation: {len(eligible)}")

        if not eligible:
            print("No specs are eligible for compilation.")
            sys.exit(0)

        compiled_count = 0
        failed_count = 0

        for hls in eligible:
            success = await compile_hls(
                hls=hls,
                feature_title=feature.title,
                project_id=feature.project_id,
                intent_md=intent_md,
                local_path=local_path,
                store=store,
                fm=fm,
                debug=debug,
            )
            if success:
                compiled_count += 1
            else:
                failed_count += 1

        print("\n--- Compilation Summary ---")
        print(f"Compiled: {compiled_count}")
        print(f"Failed:   {failed_count}")
        print(f"Skipped (not eligible): {len(specs) - len(eligible)}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
