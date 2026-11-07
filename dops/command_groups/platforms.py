from __future__ import annotations

import argparse

from ..argparse_utils import DopsHelpFormatter, add_examples
from ..installer import build_platforms
from ..platforms import load_platforms
from ..resources import ensure_skill_repo_cache, find_platforms_dir, find_skill_source_dir, resolve_local_skill_repo
from ..ui import console


def run_platform_list(*, refresh: bool = False) -> None:
    if refresh:
        ensure_skill_repo_cache(refresh=True)
    platforms = load_platforms(find_platforms_dir())
    for platform_def in platforms.values():
        caps = ", ".join(
            capability
            for capability, supported in (
                ("skill", bool(platform_def.skill and platform_def.skill.supported)),
                ("mcp", bool(platform_def.mcp and platform_def.mcp.supported)),
            )
            if supported
        )
        console.print(f"{platform_def.id.ljust(16)} {platform_def.display_name.ljust(16)} [{caps}]", markup=False)


def run_platform_build(flags: argparse.Namespace) -> None:
    if flags.source_dir:
        platforms_dir, source_dir = resolve_local_skill_repo(flags.source_dir)
    else:
        platforms_dir = find_platforms_dir()
        source_dir = find_skill_source_dir()
    results = build_platforms(
        {
            "platforms_dir": platforms_dir,
            "selected_platforms": flags.platform,
            "output_dir": flags.output_dir or "build",
            "source_dir": source_dir,
            "skill_name": flags.skill_name,
            "server_name": flags.server_name,
            "server_url": flags.server_url,
        }
    )
    for result in results:
        console.print(f"Built {result['platform_id']} -> {result['output_path']}")


def register_platform_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    platform_parser = subparsers.add_parser("platform", formatter_class=DopsHelpFormatter, help="Platform registry operations", description="Platform registry operations")
    platform_parser.set_defaults(func=lambda args: platform_parser.print_help() or 0)
    platform_subparsers = platform_parser.add_subparsers(dest="platform_command")
    add_examples(platform_parser, ["dops platform list", "dops platform build codex --output-dir build"])

    platform_list = platform_subparsers.add_parser("list", formatter_class=DopsHelpFormatter, help="List supported platforms", description="List supported platforms")
    platform_list.add_argument("--refresh", action="store_true", help="Download the current published skill and platform catalog first")
    platform_list.set_defaults(func=lambda args: run_platform_list(refresh=args.refresh))

    platform_build = platform_subparsers.add_parser("build", formatter_class=DopsHelpFormatter, help="Build platform bundles", description="Build platform bundles")
    platform_build.add_argument("platform", nargs="*", help="Platform ID(s) to build (defaults to all supported platforms)")
    platform_build.add_argument("--output-dir", help="Output directory for built platform bundles (default: build)")
    platform_build.add_argument("--source-dir", help="Local skill repository or decision-ops bundle directory")
    platform_build.add_argument("--skill-name", help="Skill bundle name override (default: decision-ops)")
    platform_build.add_argument("--server-name", help="MCP server name override (default: decision-ops-mcp)")
    platform_build.add_argument("--server-url", help="MCP server URL override")
    platform_build.set_defaults(func=run_platform_build)
    add_examples(
        platform_build,
        [
            "dops platform build codex --output-dir build",
            "dops platform build claude-code --source-dir ./skill/decision-ops",
        ],
    )
