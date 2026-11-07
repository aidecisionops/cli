from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    return subprocess.run(
        [sys.executable, "-m", "dops", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def run_cli_with_env(env_overrides: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "dops", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class CliTests(unittest.TestCase):
    def test_root_help(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("dops", result.stdout)
        self.assertIn("usage:", result.stdout)
        self.assertIn("Use dops to:", result.stdout)
        self.assertIn("Authenticate and configure the CLI", result.stdout)
        self.assertIn("Bind and verify a repository", result.stdout)
        self.assertIn("Examples:", result.stdout)
        self.assertIn("config show", result.stdout)
        self.assertIn("dops install", result.stdout)

    def test_version(self) -> None:
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+(?:-[\w.-]+)?$")

    def test_update_flag_alias(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("dops update", result.stdout)

    def test_login_help_lists_token_flag(self) -> None:
        result = run_cli("login", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Authenticate", result.stdout)
        self.assertIn("--api-base-url", result.stdout)
        self.assertIn("--token", result.stdout)

    def test_install_help_lists_supported_platforms(self) -> None:
        result = run_cli("install", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Install DecisionOps skill", result.stdout)
        self.assertIn("Platform means the editor or coding agent target", result.stdout)
        self.assertIn("dops platform list", result.stdout)

    def test_uninstall_help_explains_platform_meaning(self) -> None:
        result = run_cli("uninstall", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Platform means the editor or coding agent target", result.stdout)

    def test_auth_status_accepts_env_token(self) -> None:
        result = run_cli_with_env({"DECISIONOPS_ACCESS_TOKEN": "dop_test_token"}, "auth", "status")
        self.assertEqual(result.returncode, 0)
        self.assertIn("configured", result.stdout)
        self.assertIn("env:DECISIONOPS_ACCESS_TOKEN", result.stdout)

    def test_auth_subcommand_typos_get_suggestion(self) -> None:
        result = run_cli("auth", "statys")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Did you mean 'status'?", result.stderr)

    def test_config_show_explains_difference_from_manifest(self) -> None:
        result = run_cli("config", "show")
        self.assertEqual(result.returncode, 0)
        self.assertIn("CLI config (`config.toml`)", result.stdout)
        self.assertIn("repo binding manifest", result.stdout)

    def test_malformed_config_is_reported_in_config_show(self) -> None:
        bad_config = REPO_ROOT / ".tmp-bad-config.toml"
        bad_config.write_text("verbose = [\n", encoding="utf8")
        try:
            result = run_cli_with_env({"DECISIONOPS_CONFIG_PATH": str(bad_config)}, "config", "show")
        finally:
            bad_config.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("config_error:", result.stdout)
        self.assertIn("Invalid TOML", result.stdout)

    def test_publish_rejects_non_integer_version_before_auth(self) -> None:
        result = run_cli("publish", "dec_123", "--version", "abc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid int value", result.stderr)

    def test_bare_platform_shows_subcommand_help(self) -> None:
        result = run_cli("platform")
        self.assertEqual(result.returncode, 0)
        self.assertIn("platform", result.stdout)

    def test_bare_auth_shows_subcommand_help(self) -> None:
        result = run_cli("auth")
        self.assertEqual(result.returncode, 0)
        self.assertIn("auth", result.stdout)

    def test_bare_decisions_shows_subcommand_help(self) -> None:
        result = run_cli("decisions")
        self.assertEqual(result.returncode, 0)
        self.assertIn("decisions", result.stdout)

    def test_bare_config_shows_subcommand_help(self) -> None:
        result = run_cli("config")
        self.assertEqual(result.returncode, 0)
        self.assertIn("config", result.stdout)

    def test_decisions_list_rejects_non_integer_limit_before_auth(self) -> None:
        result = run_cli("decisions", "list", "--limit", "abc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid int value", result.stderr)
