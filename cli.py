"""Local CLI: hermes us-census schedule, unschedule, or status."""

from __future__ import annotations

import argparse
from pathlib import Path

from .public_cron import Store, schedule, status, unschedule
from .vintage import hermes_home


def setup_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="command", required=True)
    sched = sub.add_parser("schedule", help="Write the morning script and one no-agent cron job")
    sched.add_argument("--deliver", default="local", help="Only local is accepted. local does not send to chat.")
    sub.add_parser("unschedule", help="Remove the cron job and the script")
    sub.add_parser("status", help="Show the saved job and vintage. Does not fetch.")


def handle(args: argparse.Namespace) -> int:
    command = getattr(args, "command", None)
    home = hermes_home()
    store = Store()
    if command == "schedule":
        plugin_dir = Path(__file__).resolve().parent
        code, text = schedule(store, home, plugin_dir, getattr(args, "deliver", "local"))
    elif command == "unschedule":
        code, text = unschedule(store, home)
    elif command == "status":
        code, text = status(store, home)
    else:
        print("Use schedule, unschedule, or status.")
        return 1
    print(text)
    return code
