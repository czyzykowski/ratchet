"""Register a git repository as a Ratchet-managed project."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register a local git repository as a Ratchet-managed project."
    )
    parser.add_argument("--name", required=True, help="Project name")
    parser.add_argument(
        "--path",
        required=True,
        metavar="repo_path",
        help=(
            "Path to the local git repository. "
            "Must contain CLAUDE.md and docs/INTENT.md unless --config-source db is set."
        ),
    )
    parser.add_argument(
        "--config-source",
        choices=["disk", "db"],
        default="disk",
        dest="config_source",
        help=(
            "Where config files (CLAUDE.md, INTENT.md, ratchet.yaml) are stored. "
            "When 'db', file existence checks are skipped at registration time. "
            "Use set-project-config.py to upload content. Default: disk."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    from core.db import close_pool
    from core.project_manager import OnboardingError, ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        # In v1, repo_url and local_path are set to the same value.
        project = await pm.register_project(
            name=args.name,
            repo_url=args.path,
            local_path=args.path,
            config_source=args.config_source,
        )
        print(f"Registered project: {project.name}")
        print(f"Project ID: {project.id}")
        print(f"Repo: {project.local_path}")
        print(f"Config source: {project.config_source}")
    except OnboardingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
