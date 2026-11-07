from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from dops.auth_trigger import (
    describe_trigger,
    execute_cli_trigger,
    triggers_by_reason,
    _render_trigger,
)
from dops.generated.platform_models import AuthTrigger


def _ctx() -> dict[str, str]:
    return {
        "display_name": "Codex",
        "mcp_server_name": "decision-ops-mcp",
        "mcp_server_url": "https://api.aidecisionops.com/mcp",
        "skill_name": "decision-ops",
        "repo_path": "/path/to/repo",
    }


class RenderTriggerTests(unittest.TestCase):
    def test_cli_template_substitution(self) -> None:
        trigger = AuthTrigger(
            kind="cli",
            reason="primary",
            label="Authenticate",
            command=["codex", "mcp", "login", "{mcp_server_name}"],
        )
        rendered = _render_trigger(trigger, _ctx())
        assert rendered.command == ["codex", "mcp", "login", "decision-ops-mcp"]

    def test_hint_template_substitution(self) -> None:
        trigger = AuthTrigger(
            kind="slash",
            reason="primary",
            label="Slash",
            hint="Inside {display_name}, run /mcp and select {mcp_server_name}.",
        )
        rendered = _render_trigger(trigger, _ctx())
        self.assertIn("Inside Codex, run /mcp", rendered.hint or "")
        self.assertIn("decision-ops-mcp", rendered.hint or "")


class DescribeTriggerTests(unittest.TestCase):
    def test_cli_renders_joined_argv(self) -> None:
        t = AuthTrigger(kind="cli", command=["codex", "mcp", "login", "x"])
        self.assertEqual(describe_trigger(t), "codex mcp login x")

    def test_manual_falls_back_to_hint(self) -> None:
        t = AuthTrigger(kind="manual", hint="Toggle in MCP Store")
        self.assertEqual(describe_trigger(t), "Toggle in MCP Store")

    def test_fallback_when_missing_hint_and_label(self) -> None:
        t = AuthTrigger(kind="palette")
        self.assertIn("palette", describe_trigger(t))


class TriggersByReasonTests(unittest.TestCase):
    def test_filters_preserving_order(self) -> None:
        triggers = [
            AuthTrigger(kind="cli", reason="primary"),
            AuthTrigger(kind="cli", reason="reset"),
            AuthTrigger(kind="cli", reason="primary"),
        ]
        primary = triggers_by_reason(triggers, "primary")
        self.assertEqual(len(primary), 2)
        self.assertTrue(all((t.reason or "primary") == "primary" for t in primary))

    def test_missing_reason_defaults_to_primary(self) -> None:
        triggers = [AuthTrigger(kind="cli", reason=None)]
        self.assertEqual(len(triggers_by_reason(triggers, "primary")), 1)


class ExecuteCliTriggerTests(unittest.TestCase):
    def test_non_cli_trigger_is_described(self) -> None:
        t = AuthTrigger(kind="manual", hint="do a thing")
        result = execute_cli_trigger(t)
        self.assertEqual(result.status, "described")

    def test_missing_binary_reports_failure(self) -> None:
        t = AuthTrigger(kind="cli", command=["definitely-not-a-binary-xyzzy", "arg"])
        with patch("dops.auth_trigger.shutil.which", return_value=None):
            result = execute_cli_trigger(t)
        self.assertEqual(result.status, "failed")
        self.assertIn("not on PATH", result.detail)

    def test_non_zero_exit_reports_failure(self) -> None:
        t = AuthTrigger(kind="cli", command=["codex", "mcp", "login", "x"])
        with patch("dops.auth_trigger.shutil.which", return_value="/usr/bin/codex"):
            with patch(
                "dops.auth_trigger.subprocess.run",
                return_value=subprocess.CompletedProcess(args=t.command, returncode=1),
            ):
                result = execute_cli_trigger(t)
        self.assertEqual(result.status, "failed")
        self.assertIn("exited 1", result.detail)

    def test_success_reports_ran(self) -> None:
        t = AuthTrigger(kind="cli", command=["codex", "mcp", "login", "x"])
        with patch("dops.auth_trigger.shutil.which", return_value="/usr/bin/codex"):
            with patch(
                "dops.auth_trigger.subprocess.run",
                return_value=subprocess.CompletedProcess(args=t.command, returncode=0),
            ):
                result = execute_cli_trigger(t)
        self.assertEqual(result.status, "ran")


if __name__ == "__main__":
    unittest.main()
