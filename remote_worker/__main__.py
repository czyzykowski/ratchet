import argparse
import asyncio
import logging
import sys
import time

from remote_worker.client import ClaudeAuthError, RemoteWorkerClient, verify_claude_auth


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratchet remote worker")
    parser.add_argument(
        "--orchestrator",
        required=True,
        metavar="URL",
        help="Orchestrator base URL, e.g. http://host:8765",
    )
    parser.add_argument(
        "--capabilities",
        default="",
        metavar="CAP1,CAP2",
        help="Comma-separated capability list",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        verify_claude_auth()
    except ClaudeAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    client = RemoteWorkerClient(args.orchestrator, capabilities)

    delay = 1.0
    while True:
        try:
            asyncio.run(client.run())
            delay = 1.0
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(
                f"Connection error: {exc}. Reconnecting in {delay:.0f}s\u2026",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


if __name__ == "__main__":
    main()
