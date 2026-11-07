from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..manifest import manifest_summary

from ..argparse_utils import DopsHelpFormatter, add_examples
from ..config import effective_config
from ..ui import console


def run_config_show() -> None:
    config = effective_config()
    console.print("Effective DecisionOps CLI config (`config.toml`)")
    console.print("[dim]This is the CLI/user config, not the repo binding manifest at `.decisionops/manifest.toml`.[/dim]")
    for key, value in config.items():
        console.print(f"  {key}: {value}")


def run_config_path() -> None:
    console.print(f"CLI config (`config.toml`): {effective_config()['config_path']}")


def register_config_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = subparsers.add_parser(
        "config",
        formatter_class=DopsHelpFormatter,
        help="Inspect the active dops configuration",
        description="Inspect the active dops configuration",
    )
    config.set_defaults(func=lambda args: config.print_help() or 0)
    config_subparsers = config.add_subparsers(dest="config_command")
    add_examples(config, ["dops config show", "dops config path"])

    show = config_subparsers.add_parser(
        "show",
        formatter_class=DopsHelpFormatter,
        help="Show the effective configuration values",
        description="Show the effective configuration values",
    )
    show.set_defaults(func=lambda args: run_config_show())

    path = config_subparsers.add_parser(
        "path",
        formatter_class=DopsHelpFormatter,
        help="Show the config file path dops will read",
        description="Show the config file path dops will read",
    )
    path.set_defaults(func=lambda args: run_config_path())


def register_manifest_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    manifest = subparsers.add_parser("manifest", help="Read the repository binding without authentication")
    commands = manifest.add_subparsers(dest="manifest_command")
    manifest.set_defaults(func=lambda args: manifest.print_help() or 0)
    show = commands.add_parser("show", help="Validate the repository manifest and emit binding fields as JSON")
    show.add_argument("--repo-path", default=".", help="Repository root (defaults to current directory)")
    show.set_defaults(func=lambda args: print(json.dumps(manifest_summary(str(Path(args.repo_path).resolve())), indent=2)))
