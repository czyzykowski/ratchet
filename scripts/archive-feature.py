"""Abandon a feature by transitioning it to the 'abandoned' terminal status."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Abandon a feature, transitioning it to the 'abandoned' terminal status."
    )
    parser.add_argument("--feature-id", required=True, help="Feature UUID to abandon")
    parser.add_argument("--reason", default=None, help="Optional reason for abandonment")
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    try:
        feature_id = UUID(args.feature_id)
    except ValueError:
        print(f"Error: invalid feature-id: {args.feature_id!r}", file=sys.stderr)
        sys.exit(1)

    answer = input(f"Abandon feature {feature_id}? [y/N]: ").strip().lower()
    if answer != "y":
        print("Aborted.")
        sys.exit(0)

    from core.db import close_pool
    from core.feature_manager import FeatureManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        fm = FeatureManager(store)
        await fm.abandon_feature(feature_id, reason=args.reason)
        print(f"Feature {feature_id} abandoned.")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
