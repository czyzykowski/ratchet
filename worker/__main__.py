import argparse

from worker.runner import main, main_loop_entry

parser = argparse.ArgumentParser(description="Ratchet worker")
parser.add_argument(
    "--once",
    action="store_true",
    help="Run a single pass (process one implementation task and one QA task) then exit",
)
args = parser.parse_args()

if args.once:
    main()
else:
    main_loop_entry()
