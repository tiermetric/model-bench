"""Command-line entry point: `python3 -m orgsync <command>`.

Commands: `version`, `show-config`, `sync`. Dispatch lives here and nothing
else does — each command is one small function turning parsed arguments into a
call on the package, so adding a command never reshapes the parser.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .service import ServiceError
from .sync import DEFAULT_OUT_DIR, run_sync

PROG = "orgsync"

#: Exit code for a configuration problem. 0 is success and 2 is argparse's
#: usage error, so user-facing failures start at 3.
EXIT_CONFIG_ERROR = 3
#: Exit code for a sync that could not complete against the services.
EXIT_SYNC_ERROR = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Kept separate so tests can inspect it."""
    parser = argparse.ArgumentParser(
        prog=PROG, description="Reconcile Directory work items with Ledger accounts.")
    parser.add_argument("--version", action="version",
                        version="%s %s" % (PROG, __version__))
    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)

    subparsers.add_parser("version", help="print the orgsync version")
    show_config = subparsers.add_parser("show-config", help="print the service URLs")
    sync = subparsers.add_parser("sync", help="reconcile the two services")
    for sub in (show_config, sync):
        sub.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                         help="path to config.json (default: %(default)s)")
    sync.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                      help="directory for output files (default: %(default)s)")
    return parser


def _cmd_version(args: argparse.Namespace) -> int:
    print("%s %s" % (PROG, __version__))
    return 0


def _cmd_show_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print("directory_url: %s" % config.directory_url)
    print("ledger_url: %s" % config.ledger_url)
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    return run_sync(load_config(args.config), args.out_dir)


#: command name -> handler. The parser guarantees the key exists.
COMMANDS = {"version": _cmd_version, "show-config": _cmd_show_config, "sync": _cmd_sync}


def main(argv: Optional[List[str]] = None) -> int:
    """Parse `argv` (default `sys.argv[1:]`), run the command, return its code.

    A configuration problem is reported on stderr as EXIT_CONFIG_ERROR, and a
    sync that cannot complete against the services as EXIT_SYNC_ERROR, rather
    than raised, so the command line stays usable; anything else propagates,
    because an unexpected failure should be seen with its traceback.
    """
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return COMMANDS[args.command](args)
    except ConfigError as exc:
        print("%s: %s" % (PROG, exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ServiceError as exc:
        print("%s: sync failed: %s" % (PROG, exc), file=sys.stderr)
        return EXIT_SYNC_ERROR


if __name__ == "__main__":
    sys.exit(main())
