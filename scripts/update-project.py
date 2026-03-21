"""Update a Ratchet-managed project's required capabilities."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update required capabilities of a Ratchet-managed project."
    )
    parser.add_argument("--project-id", required=True, dest="project_id", help="Project UUID")
    parser.add_argument(
        "--capabilities",
        required=True,
        dest="capabilities",
        metavar="CAPABILITIES",
        help="Comma-separated list of required capabilities (e.g. 'osx,gpu'). Use '' to clear.",
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    if args.capabilities:
        capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    else:
        capabilities = []

    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        project_id = UUID(args.project_id)
        existing = await pm.get_project(project_id)
        if existing is None:
            print(f"Error: project {project_id} not found", file=sys.stderr)
            sys.exit(1)
        await pm.update_project(
            project_id,
            name=existing.name,
            repo_url=existing.repo_url,
            local_path=existing.local_path,
            config_source=existing.config_source,
            required_capabilities=capabilities,
        )
        print(f"Updated project: {existing.name}")
        print(f"Project ID: {project_id}")
        print(f"Required capabilities: {', '.join(capabilities) if capabilities else '(none)'}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
