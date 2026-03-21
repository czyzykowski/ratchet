"""Entry point: python -m orchestrator."""
from __future__ import annotations

import argparse
import logging

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Ratchet orchestrator server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", default=8765, type=int, help="Bind port (default: 8765)")
    args = parser.parse_args()
    uvicorn.run("orchestrator.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
