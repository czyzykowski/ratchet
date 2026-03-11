"""Upload config file contents for a project to the Ratchet database."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upload config file contents (CLAUDE.md, INTENT.md, ratchet.yaml) "
            "for a DB-backed project."
        )
    )
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument(
        "--claude-md",
        metavar="path",
        help="Path to CLAUDE.md file to upload",
    )
    parser.add_argument(
        "--intent-md",
        metavar="path",
        help="Path to INTENT.md file to upload",
    )
    parser.add_argument(
        "--ratchet-yaml",
        metavar="path",
        help="Path to ratchet.yaml file to upload",
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    from uuid import UUID

    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    try:
        project_id = UUID(args.project_id)
    except ValueError:
        print(f"Error: invalid project-id: {args.project_id}", file=sys.stderr)
        sys.exit(1)

    claude_md: str | None = None
    intent_md: str | None = None
    ratchet_yaml: str | None = None

    if args.claude_md:
        p = Path(args.claude_md)
        if not p.exists():
            print(f"Error: file not found: {args.claude_md}", file=sys.stderr)
            sys.exit(1)
        claude_md = p.read_text()

    if args.intent_md:
        p = Path(args.intent_md)
        if not p.exists():
            print(f"Error: file not found: {args.intent_md}", file=sys.stderr)
            sys.exit(1)
        intent_md = p.read_text()

    if args.ratchet_yaml:
        p = Path(args.ratchet_yaml)
        if not p.exists():
            print(f"Error: file not found: {args.ratchet_yaml}", file=sys.stderr)
            sys.exit(1)
        ratchet_yaml = p.read_text()

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        await pm.update_project_config(
            project_id=project_id,
            claude_md=claude_md,
            intent_md=intent_md,
            ratchet_yaml=ratchet_yaml,
        )
        print(f"Config updated for project {project_id}")
        if claude_md is not None:
            print(f"  CLAUDE.md: {len(claude_md)} bytes")
        if intent_md is not None:
            print(f"  INTENT.md: {len(intent_md)} bytes")
        if ratchet_yaml is not None:
            print(f"  ratchet.yaml: {len(ratchet_yaml)} bytes")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
