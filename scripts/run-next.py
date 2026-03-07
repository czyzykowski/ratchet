"""Pick the oldest ready_for_implementation task and execute it via the worker."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from worker.runner import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
