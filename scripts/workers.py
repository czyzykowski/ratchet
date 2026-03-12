"""Print connected workers from the orchestrator."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    orchestrator_url = os.environ.get("ORCHESTRATOR_URL")
    if not orchestrator_url:
        print("Error: ORCHESTRATOR_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    url = orchestrator_url.rstrip("/") + "/workers"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status != 200:
                print(f"Error: unexpected status {response.status} from {url}", file=sys.stderr)
                sys.exit(1)
            workers = json.loads(response.read())
    except urllib.error.URLError as exc:
        print(f"Error: could not reach orchestrator at {url}: {exc}", file=sys.stderr)
        sys.exit(1)

    print("=== RATCHET WORKERS ===")

    if not workers:
        print("\nNo workers connected.")
        return

    for w in workers:
        worker_id = w["id"][:8]
        capabilities = w.get("capabilities") or []
        caps_str = ", ".join(capabilities)
        exec_id = w.get("current_execution_id")
        status = f"executing {exec_id[:8]}" if exec_id else "idle"
        connected_at_raw = w.get("connected_at", "")
        # Strip microseconds: "2026-01-01T12:00:00.123456" -> "2026-01-01T12:00:00"
        connected_at = connected_at_raw.split(".")[0] if connected_at_raw else connected_at_raw
        print(f"  [{worker_id}] {caps_str}  —  {status}  —  connected {connected_at}")


if __name__ == "__main__":
    main()
