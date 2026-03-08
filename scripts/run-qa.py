"""Pick the oldest ready_for_qa task and run one QA cycle."""

from __future__ import annotations

import asyncio
import logging
import os
import sys


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main_async())


async def _main_async() -> None:
    from core.db import close_pool
    from core.invoker import ClaudeCodeInvoker
    from core.store import PostgresStore
    from worker.runner import run_qa_once

    store = PostgresStore()
    invoker = ClaudeCodeInvoker()
    try:
        await run_qa_once(store, invoker)
    finally:
        await close_pool()


if __name__ == "__main__":
    main()
