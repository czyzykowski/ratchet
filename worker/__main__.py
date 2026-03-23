import argparse
import asyncio
import os

from worker.remote import RemoteWorker

parser = argparse.ArgumentParser(description="Ratchet worker")
parser.add_argument(
    "--remote",
    metavar="URL",
    required=True,
    help="Orchestrator WebSocket URL (required)",
)
parser.add_argument(
    "--capabilities",
    default="",
    metavar="CAP1,CAP2",
    help="Comma-separated list of local worker capabilities (default: none)",
)
parser.add_argument(
    "--projects",
    default="",
    help="Comma-separated project_id:path pairs",
)
parser.add_argument(
    "--workspace",
    default="~/ratchet-projects",
    help="Root directory for new project clones (default: ~/ratchet-projects)",
)
args = parser.parse_args()
capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]

projects: dict[str, str] = {}
for pair in args.projects.split(","):
    pair = pair.strip()
    if ":" in pair:
        project_id, path = pair.split(":", 1)
        projects[project_id.strip()] = path.strip()

workspace = os.path.expanduser(args.workspace)

worker = RemoteWorker(args.remote, capabilities, projects, workspace=workspace)
asyncio.run(worker.run())
