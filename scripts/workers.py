#!/usr/bin/env python3
"""Show connected workers and their current state.

Usage: python scripts/workers.py
"""

from __future__ import annotations

import json
import sys
import urllib.request


def main() -> None:
    base = "http://localhost:8000"
    try:
        with urllib.request.urlopen(f"{base}/api/workers", timeout=5) as resp:
            workers = json.loads(resp.read())
    except Exception as exc:
        print(f"Cannot reach {base}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not workers:
        print("No workers connected.")
        return

    for w in workers:
        caps = ", ".join(w.get("capabilities", [])) or "(none)"
        status = w.get("status", "?")
        exec_id = w.get("current_execution_id")
        exec_str = exec_id[:12] if exec_id else "idle"
        print(f"  {w['id'][:12]}  caps=[{caps}]  {status:6s}  exec={exec_str}")


if __name__ == "__main__":
    main()
