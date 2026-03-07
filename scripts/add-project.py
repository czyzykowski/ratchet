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
        help="Path to the local git repository. Must contain CLAUDE.md and docs/INTENT.md.",
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
        )
        print(f"Registered project: {project.name}")
        print(f"Project ID: {project.id}")
        print(f"Repo: {project.local_path}")
    except OnboardingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
