from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from dops.mcp_inspect import (
    ApiAuthProbeResult,
    McpGateProbeResult,
    McpReachabilityResult,
    inspect_mcp_entry,
    probe_api_auth,
    probe_mcp_gate_resolution,
    probe_mcp_reachability,
)

EXPECTED_URL = "https://api.aidecisionops.com/mcp"
SERVER_NAME = "decision-ops-mcp"


class _TempFileMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def write(self, name: str, content: str) -> str:
        path = self.tmp_path / name
        path.write_text(content, encoding="utf8")
        return str(path)


class InspectMcpEntryCodexTomlTests(_TempFileMixin):
    def test_missing_file_reports_not_configured(self) -> None:
        report = inspect_mcp_entry(
            config_path=str(self.tmp_path / "config.toml"),
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertFalse(report.config_exists)
        self.assertFalse(report.healthy)
        self.assertTrue(any("not present" in issue for issue in report.issues))

    def test_happy_path(self) -> None:
        path = self.write(
            "config.toml",
            '[mcp_servers.decision-ops-mcp]\nurl = "https://api.aidecisionops.com/mcp"\n',
        )
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertTrue(report.healthy)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.short_status(), "ok")

    def test_detects_wrong_url(self) -> None:
        path = self.write(
            "config.toml",
            '[mcp_servers.decision-ops-mcp]\nurl = "https://old.example.com/mcp"\n',
        )
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
            platform_id="codex",
        )
        self.assertTrue(report.entry_found)
        self.assertFalse(report.url_matches)
        self.assertFalse(report.healthy)
        self.assertTrue(any("old.example.com" in issue for issue in report.issues))
        self.assertIn("wrong url", report.short_status())
        self.assertTrue(
            any("dops install codex --skip-skill --skip-manifest" in issue for issue in report.issues)
        )

    def test_missing_entry_recommends_mcp_only_install(self) -> None:
        """Doctor should tell users exactly how to fix just the MCP entry
        without re-running skill and manifest work that is already correct."""
        path = self.write("config.toml", '[mcp_servers.other]\nurl = "https://x.example.com"\n')
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
            platform_id="codex",
        )
        self.assertFalse(report.entry_found)
        self.assertTrue(
            any("dops install codex --skip-skill --skip-manifest" in issue for issue in report.issues)
        )

    def test_detects_disabled_entry(self) -> None:
        path = self.write(
            "config.toml",
            '[mcp_servers.decision-ops-mcp]\nenabled = false\nurl = "https://api.aidecisionops.com/mcp"\n',
        )
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertTrue(report.entry_found)
        self.assertTrue(report.url_matches)
        self.assertFalse(report.entry_enabled)
        self.assertFalse(report.healthy)
        self.assertTrue(any("disabled" in issue for issue in report.issues))

    def test_flags_competing_entry_pointing_to_expected_url(self) -> None:
        path = self.write(
            "config.toml",
            '[mcp_servers.legacy-decision]\nurl = "https://api.aidecisionops.com/mcp"\n',
        )
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertFalse(report.entry_found)
        self.assertEqual(report.competing_entries, ["legacy-decision"])
        self.assertTrue(any("legacy-decision" in issue for issue in report.issues))

    def test_invalid_toml_is_surfaced(self) -> None:
        path = self.write("config.toml", "not = [valid toml")
        report = inspect_mcp_entry(
            config_path=path,
            fmt="codex_toml",
            root_key=None,
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertIsNotNone(report.parse_error)
        self.assertFalse(report.healthy)


class InspectMcpEntryJsonMapTests(_TempFileMixin):
    def test_happy_path(self) -> None:
        payload = {"mcpServers": {SERVER_NAME: {"type": "http", "url": EXPECTED_URL}}}
        path = self.write("config.json", json.dumps(payload))
        report = inspect_mcp_entry(
            config_path=path,
            fmt="json_map",
            root_key="mcpServers",
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertTrue(report.healthy)

    def test_disabled_via_disabled_field(self) -> None:
        payload = {"mcpServers": {SERVER_NAME: {"type": "http", "url": EXPECTED_URL, "disabled": True}}}
        path = self.write("config.json", json.dumps(payload))
        report = inspect_mcp_entry(
            config_path=path,
            fmt="json_map",
            root_key="mcpServers",
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertFalse(report.entry_enabled)
        self.assertFalse(report.healthy)

    def test_empty_file_is_surfaced(self) -> None:
        path = self.write("config.json", "")
        report = inspect_mcp_entry(
            config_path=path,
            fmt="json_map",
            root_key="mcpServers",
            server_name=SERVER_NAME,
            expected_url=EXPECTED_URL,
        )
        self.assertFalse(report.healthy)
        self.assertTrue(any("empty" in issue.lower() for issue in report.issues))


class ProbeApiAuthTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        class FakeResponse:
            status = 200
            body = b'{"userId":"u"}'

        with patch("dops.mcp_inspect.urlopen_with_retries", return_value=FakeResponse()):
            result = probe_api_auth(api_base_url="https://api.example.com", token="tok")
        self.assertIsInstance(result, ApiAuthProbeResult)
        self.assertTrue(result.reachable)
        self.assertEqual(result.short_status(), "ok")

    def test_401_recommends_force_login(self) -> None:
        from dops.http import HttpStatusError

        with patch("dops.mcp_inspect.urlopen_with_retries") as mock:
            mock.side_effect = HttpStatusError(
                status=401,
                url="https://api.example.com/v1/auth/me",
                headers={},
                body=b'{"error":"Invalid access token"}',
                reason="Unauthorized",
            )
            result = probe_api_auth(api_base_url="https://api.example.com", token="tok")
        self.assertFalse(result.reachable)
        self.assertIn("dops login --force", result.short_status())

    def test_url_error_reports_unreachable(self) -> None:
        with patch("dops.mcp_inspect.urlopen_with_retries", side_effect=urllib.error.URLError("DNS failure")):
            result = probe_api_auth(api_base_url="https://api.example.com", token="tok")
        self.assertEqual(result.status, 0)
        self.assertFalse(result.reachable)


class ProbeMcpReachabilityTests(unittest.TestCase):
    def test_401_missing_bearer_is_considered_reachable(self) -> None:
        """Unauthenticated probe MUST see 401 to prove the server is up.

        The IDE MCP client handles its own OAuth against this endpoint,
        so the CLI can only verify the server is alive — not that our
        CLI token works there (it can't, different audience)."""
        from dops.http import HttpStatusError

        with patch("dops.mcp_inspect.urlopen_with_retries") as mock:
            mock.side_effect = HttpStatusError(
                status=401,
                url="https://api.example.com/mcp",
                headers={},
                body=b'{"error":"Missing bearer token..."}',
                reason="Unauthorized",
            )
            result = probe_mcp_reachability(mcp_url="https://api.example.com/mcp")
        self.assertIsInstance(result, McpReachabilityResult)
        self.assertTrue(result.reachable)
        self.assertIn("IDE MCP client", result.short_status())

    def test_network_error_is_unreachable(self) -> None:
        with patch("dops.mcp_inspect.urlopen_with_retries", side_effect=urllib.error.URLError("DNS failure")):
            result = probe_mcp_reachability(mcp_url="https://api.example.com/mcp")
        self.assertEqual(result.status, 0)
        self.assertFalse(result.reachable)

    def test_5xx_is_unreachable(self) -> None:
        from dops.http import HttpStatusError

        with patch("dops.mcp_inspect.urlopen_with_retries") as mock:
            mock.side_effect = HttpStatusError(
                status=503,
                url="https://api.example.com/mcp",
                headers={},
                body=b'{"error":"upstream down"}',
                reason="Service Unavailable",
            )
            result = probe_mcp_reachability(mcp_url="https://api.example.com/mcp")
        self.assertFalse(result.reachable)
        self.assertEqual(result.status, 503)


class ProbeMcpGateResolutionTests(unittest.TestCase):
    def test_extracts_bearer_token_from_json_and_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "mcp.json"
            json_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            SERVER_NAME: {
                                "url": EXPECTED_URL,
                                "headers": {"Authorization": "Bearer dops_sk_test_123"},
                            }
                        }
                    }
                ),
                encoding="utf8",
            )
            json_report = inspect_mcp_entry(
                config_path=str(json_path),
                fmt="json_map",
                root_key="mcpServers",
                server_name=SERVER_NAME,
                expected_url=EXPECTED_URL,
            )
            self.assertEqual(json_report.bearer_token, "dops_sk_test_123")

            toml_path = Path(tmp) / "config.toml"
            toml_path.write_text(
                '[mcp_servers.decision-ops-mcp]\nurl = "https://api.aidecisionops.com/mcp"\n'
                '[mcp_servers.decision-ops-mcp.http_headers]\nAuthorization = "Bearer dops_sk_toml_456"\n',
                encoding="utf8",
            )
            toml_report = inspect_mcp_entry(
                config_path=str(toml_path),
                fmt="codex_toml",
                root_key=None,
                server_name=SERVER_NAME,
                expected_url=EXPECTED_URL,
            )
            self.assertEqual(toml_report.bearer_token, "dops_sk_toml_456")

    def test_probe_mcp_gate_resolution_reports_mapped_repo_and_provisioning_support(self) -> None:
        class FakeToolsResponse:
            status = 200
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tools": [
                            {
                                "name": "do-prepare-decision-gate",
                                "inputSchema": {
                                    "properties": {
                                        "create_project_if_missing": {"type": "boolean"},
                                        "project_name": {"type": "string"},
                                    }
                                },
                            },
                            {
                                "name": "do-create-decision-draft",
                                "inputSchema": {
                                    "properties": {
                                        "create_project_if_missing": {"type": "boolean"},
                                    }
                                },
                            },
                        ]
                    },
                }
            ).encode("utf8")

        class FakeGateResponse:
            status = 200
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "structuredContent": {
                            "org_id": "acme-engineering",
                            "project_id": "36f2fc32-fb1f-4810-ad5c-814ed5ca5dce",
                            "project_resolution": "mapped_repo",
                            "available_projects": [{"id": "36f2fc32-fb1f-4810-ad5c-814ed5ca5dce"}],
                            "suggested_new_project": None,
                        }
                    },
                }
            ).encode("utf8")

        with patch("dops.mcp_inspect.urlopen_with_retries", side_effect=[FakeToolsResponse(), FakeGateResponse()]):
            result = probe_mcp_gate_resolution(
                mcp_url=EXPECTED_URL,
                token="dops_sk_test",
                project_id="36f2fc32-fb1f-4810-ad5c-814ed5ca5dce",
                repo_ref="acme/backend",
            )
        self.assertIsInstance(result, McpGateProbeResult)
        self.assertTrue(result.authenticated)
        self.assertTrue(result.supports_project_provisioning)
        self.assertEqual(result.project_resolution, "mapped_repo")
        self.assertEqual(result.resolved_project_id, "36f2fc32-fb1f-4810-ad5c-814ed5ca5dce")
        self.assertIn("resolution=mapped_repo", result.short_status())


if __name__ == "__main__":
    unittest.main()
