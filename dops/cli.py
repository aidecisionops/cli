from __future__ import annotations

import argparse
import traceback

from . import __version__
from .argparse_utils import DopsArgumentParser, DopsHelpFormatter, add_examples
from .command_groups import register_all_commands
from .command_groups.update import run_update
from .config import DEFAULT_DEBUG, DEFAULT_VERBOSE
from .runtime import is_debug, set_diagnostics
from .ui import CancelledError, error_console

ROOT_HELP_SECTIONS = [
    {
        "title": "Authenticate and configure the CLI",
        "commands": [
            ("login", "Authenticate this machine with DecisionOps"),
            ("logout", "Revoke and remove the local DecisionOps session"),
            ("auth status", "Inspect the current DecisionOps auth session"),
            ("config show", "Show active CLI config (`config.toml`) values"),
        ],
    },
    {
        "title": "Bind and verify a repository",
        "commands": [
            ("init", "Bind the current repository to a DecisionOps project"),
            ("install", "Install DecisionOps skill + MCP config"),
            ("uninstall", "Remove installed skills, MCP entries, and local auth state"),
            ("doctor", "Diagnose local setup and repo binding issues"),
        ],
    },
    {
        "title": "Work with decisions and governance",
        "commands": [
            ("decisions", "List, get, search, or create decisions"),
            ("gate", "Run decision gate on the current task"),
            ("validate", "Validate a decision against org constraints"),
            ("publish", "Publish a proposed decision"),
            ("status", "Show governance coverage, health, drift, and alerts"),
        ],
    },
    {
        "title": "Maintain the CLI and platform bundles",
        "commands": [
            ("platform", "Inspect or build platform bundles"),
            ("update", "Update the dops CLI to the latest release"),
        ],
    },
]


def build_parser() -> argparse.ArgumentParser:
    parser = DopsArgumentParser(
        prog="dops",
        formatter_class=DopsHelpFormatter,
        description="dops — repo-anchored CLI for working with decisions\n\nRespects NO_COLOR and FORCE_COLOR environment variables.",
    )
    parser.add_argument("--update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", action="store_true", default=DEFAULT_VERBOSE, help="Show diagnostic output")
    parser.add_argument("--debug", action="store_true", default=DEFAULT_DEBUG, help="Show diagnostic output and tracebacks")
    subparsers = parser.add_subparsers(dest="command")
    add_examples(
        parser,
        [
            "dops login",
            "dops init --org-id acme --project-id backend --repo-ref acme/backend",
            "dops install",
            "dops update",
            "dops doctor",
        ],
    )
    register_all_commands(subparsers)
    parser._root_help_sections = ROOT_HELP_SECTIONS  # type: ignore[attr-defined]
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_diagnostics(verbose=bool(getattr(args, "verbose", False)), debug=bool(getattr(args, "debug", False)))
    if getattr(args, "update", False):
        args.version = getattr(args, "version", None)
        args.install_dir = getattr(args, "install_dir", None)
        args.func = run_update
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        result = args.func(args)
        return int(result) if isinstance(result, int) else 0
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except CancelledError:
        print("\nCancelled.")
        return 0
    except Exception as error:
        error_console.print(str(error))
        if is_debug():
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
