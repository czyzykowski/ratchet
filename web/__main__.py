"""Entry point: python -m web starts the Ratchet web server."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
