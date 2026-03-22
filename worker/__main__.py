import argparse
import asyncio

from worker.runner import main, main_loop_entry

parser = argparse.ArgumentParser(description="Ratchet worker")
parser.add_argument(
    "--once",
    action="store_true",
    help="Run a single pass (process one implementation task and one QA task) then exit",
)
parser.add_argument(
    "--watchdog-timeout",
    type=int,
    default=300,
    metavar="SECONDS",
    help="Seconds of silence before watchdog warning (default: 300)",
)
parser.add_argument(
    "--capabilities",
    default="",
    metavar="CAP1,CAP2",
    help="Comma-separated list of local worker capabilities (default: none)",
)
parser.add_argument(
    "--remote",
    metavar="URL",
    help="Orchestrator URL for remote mode",
)
parser.add_argument(
    "--projects",
    default="",
    help="Comma-separated project_id:path pairs (used with --remote)",
)
args = parser.parse_args()
capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]

if args.remote:
    from worker.remote import RemoteWorker

    projects: dict[str, str] = {}
    for pair in args.projects.split(","):
        pair = pair.strip()
        if ":" in pair:
            project_id, path = pair.split(":", 1)
            projects[project_id.strip()] = path.strip()

    worker = RemoteWorker(args.remote, capabilities, projects)
    asyncio.run(worker.run())
elif args.once:
    main(watchdog_timeout=args.watchdog_timeout, local_capabilities=capabilities)
else:
    main_loop_entry(
        watchdog_timeout=args.watchdog_timeout, local_capabilities=capabilities
    )
