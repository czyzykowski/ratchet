"""Entry point: python -m web starts the Ratchet web server."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Ratchet web server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WEB_PORT", "8000")),
        help="Bind port (default: 8000)",
    )
    parser.add_argument(
        "--dispatch",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable the dispatch loop (default: from DISPATCH_ENABLED env var, true)",
    )
    args = parser.parse_args()

    # Set WEB_PORT so LocalWorkerManager knows where to connect
    os.environ["WEB_PORT"] = str(args.port)

    # Set DISPATCH_ENABLED if flag was explicitly passed
    if args.dispatch is not None:
        os.environ["DISPATCH_ENABLED"] = "true" if args.dispatch else "false"

    import uvicorn

    uvicorn.run("web.app:app", host="0.0.0.0", port=args.port, reload=False)


if __name__ == "__main__":
    main()
