from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dops.installer import (
    _remove_codex_toml_server,
    _remove_empty_dir_if_present,
    _remove_file_if_present,
    _remove_json_map_server,
    _upsert_codex_toml,
    _upsert_json_map,
    _validate_toml_key,
    build_platform,
    cleanup_platforms,
    install_platforms,
)
from dops.platforms import load_platforms


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="dops-installer-test-")
        self.repo_path = Path(self.temp_dir) / "repo"
        self.repo_path.mkdir()
        self.platforms_dir = Path(self.temp_dir) / "platforms"
        self.platforms_dir.mkdir()
        (self.platforms_dir / "test.toml").write_text(
            "\n".join(
                [
                    'id = "test"',
                    'display_name = "Test Platform"',
                    "",
                    "[skill]",
                    "supported = true",
                    'install_path_default = "{repo_path}/.skills/{skill_name}"',
                    'build_path = "{skill_name}"',
                    "",
                    "[mcp]",
                    "supported = true",
                    'scope = "project"',
                    'format = "json_map"',
                    'root_key = "mcpServers"',
                    'install_path_default = "{repo_path}/.mcp.json"',
                    "",
                    "[auth]",
                    'mode = "interactive_handoff"',
                    'instructions = ["Enable {display_name}"]',
                    "",
                ]
            ),
            encoding="utf8",
        )
        self.skill_source = Path(self.temp_dir) / "skill"
        self.skill_source.mkdir()
        (self.skill_source / "SKILL.md").write_text("# Demo skill\n", encoding="utf8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_install_and_cleanup_round_trip(self) -> None:
        result = install_platforms(
            {
                "platforms_dir": str(self.platforms_dir),
                "selected_platforms": ["test"],
                "skill_name": "decision-ops",
                "source_dir": str(self.skill_source),
                "repo_path": str(self.repo_path),
                "install_skill": True,
                "install_mcp": True,
                "write_manifest": True,
                "org_id": "acme",
                "project_id": "backend",
                "repo_ref": "acme/backend",
                "default_branch": "main",
            }
        )
        self.assertTrue((self.repo_path / ".skills" / "decision-ops" / "SKILL.md").exists())
        self.assertTrue((self.repo_path / ".mcp.json").exists())
        self.assertTrue((self.repo_path / ".decisionops" / "manifest.toml").exists())
        self.assertEqual(result.installed_skills[0]["platformId"], "test")

        cleanup = cleanup_platforms(
            {
                "platforms_dir": str(self.platforms_dir),
                "selected_platforms": ["test"],
                "skill_name": "decision-ops",
                "repo_path": str(self.repo_path),
                "remove_skill": True,
                "remove_mcp": True,
                "remove_manifest": True,
            }
        )
        self.assertFalse((self.repo_path / ".skills" / "decision-ops").exists())
        self.assertFalse((self.repo_path / ".mcp.json").exists())
        self.assertFalse((self.repo_path / ".decisionops" / "manifest.toml").exists())
        self.assertEqual(cleanup.removed_skills[0]["platformId"], "test")

    def test_install_reuses_existing_manifest_when_ids_are_not_passed(self) -> None:
        decisionops_dir = self.repo_path / ".decisionops"
        decisionops_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = decisionops_dir / "manifest.toml"
        manifest_path.write_text(
            "\n".join(
                [
                    "version = 1",
                    'org_id = "org_existing"',
                    'project_id = "proj_existing"',
                    'repo_ref = "acme/existing"',
                    'repo_id = "repo_existing"',
                    'default_branch = "main"',
                    'mcp_server_name = "existing-mcp"',
                    'mcp_server_url = "https://existing.example.com/mcp"',
                    "",
                ]
            ),
            encoding="utf8",
        )

        result = install_platforms(
            {
                "platforms_dir": str(self.platforms_dir),
                "selected_platforms": ["test"],
                "skill_name": "decision-ops",
                "source_dir": str(self.skill_source),
                "repo_path": str(self.repo_path),
                "install_skill": True,
                "install_mcp": True,
                "write_manifest": True,
            }
        )

        manifest = manifest_path.read_text(encoding="utf8")
        mcp_config = (self.repo_path / ".mcp.json").read_text(encoding="utf8")
        self.assertEqual(result.manifest_path, str(manifest_path))
        self.assertIn('org_id = "org_existing"', manifest)
        self.assertIn('project_id = "proj_existing"', manifest)
        self.assertIn('repo_ref = "acme/existing"', manifest)
        self.assertIn('repo_id = "repo_existing"', manifest)
        self.assertIn('mcp_server_name = "existing-mcp"', manifest)
        self.assertIn('mcp_server_url = "https://existing.example.com/mcp"', manifest)
        self.assertIn('"existing-mcp"', mcp_config)
        self.assertIn("https://existing.example.com/mcp", mcp_config)

    def test_upsert_json_map_reports_corrupt_existing_file(self) -> None:
        config_path = self.repo_path / ".mcp.json"
        config_path.write_text("{broken", encoding="utf8")
        with self.assertRaises(RuntimeError) as raised:
            _upsert_json_map(str(config_path), "mcpServers", "decision-ops", "https://api.example.com/mcp")
        self.assertIn("Invalid JSON in MCP config", str(raised.exception))

    def test_remove_json_map_server_reports_corrupt_existing_file(self) -> None:
        config_path = self.repo_path / ".mcp.json"
        config_path.write_text("{broken", encoding="utf8")
        with self.assertRaises(RuntimeError) as raised:
            _remove_json_map_server(str(config_path), "mcpServers", "decision-ops")
        self.assertIn("Invalid JSON in MCP config", str(raised.exception))

    def test_remove_file_if_present_tolerates_missing_race(self) -> None:
        file_path = self.repo_path / "manifest.toml"
        file_path.write_text("hello\n", encoding="utf8")

        def simulated_unlink(path: Path, *, missing_ok: bool = False) -> None:
            self.assertTrue(missing_ok)

        with mock.patch("dops.installer.Path.unlink", autospec=True, side_effect=simulated_unlink):
            self.assertTrue(_remove_file_if_present(str(file_path)))

    def test_build_platform_wraps_existing_output_removal_error(self) -> None:
        output_dir = Path(self.temp_dir) / "build"
        platform_output = output_dir / "test"
        platform_output.mkdir(parents=True)
        platform = load_platforms(str(self.platforms_dir))["test"]

        with mock.patch("dops.installer.shutil.rmtree", side_effect=OSError("directory is locked")):
            with self.assertRaises(RuntimeError) as raised:
                build_platform(
                    platform,
                    "decision-ops",
                    str(self.skill_source),
                    str(output_dir),
                    "decision-ops-mcp",
                    "https://api.example.com/mcp",
                )

        self.assertIn("Could not clear existing build output", str(raised.exception))

    def test_upsert_codex_toml_reports_non_utf8_file(self) -> None:
        config_path = self.repo_path / "codex.toml"
        config_path.write_bytes(b"\xff\xfe broken")
        with self.assertRaises(RuntimeError) as raised:
            _upsert_codex_toml(str(config_path), "decision-ops", "https://api.example.com/mcp")
        self.assertIn("Cannot read MCP config", str(raised.exception))

    def test_remove_codex_toml_server_reports_non_utf8_file(self) -> None:
        config_path = self.repo_path / "codex.toml"
        config_path.write_bytes(b"\xff\xfe broken")
        with self.assertRaises(RuntimeError) as raised:
            _remove_codex_toml_server(str(config_path), "decision-ops")
        self.assertIn("Cannot read MCP config", str(raised.exception))

    def test_validate_toml_key_rejects_special_characters(self) -> None:
        with self.assertRaises(RuntimeError):
            _validate_toml_key("bad]name", "server name")
        with self.assertRaises(RuntimeError):
            _validate_toml_key("bad\nname", "server name")
        _validate_toml_key("good-name_123", "server name")

    def test_upsert_json_map_reports_non_utf8_file(self) -> None:
        config_path = self.repo_path / ".mcp.json"
        config_path.write_bytes(b"\xff\xfe broken")
        with self.assertRaises(RuntimeError) as raised:
            _upsert_json_map(str(config_path), "mcpServers", "decision-ops", "https://api.example.com/mcp")
        self.assertIn("Cannot read MCP config", str(raised.exception))

    def test_remove_json_map_server_reports_non_utf8_file(self) -> None:
        config_path = self.repo_path / ".mcp.json"
        config_path.write_bytes(b"\xff\xfe broken")
        with self.assertRaises(RuntimeError) as raised:
            _remove_json_map_server(str(config_path), "mcpServers", "decision-ops")
        self.assertIn("Cannot read MCP config", str(raised.exception))

    def test_remove_empty_dir_tolerates_permission_error(self) -> None:
        dir_path = self.repo_path / "locked"
        dir_path.mkdir()
        with mock.patch("dops.installer.Path.rmdir", side_effect=PermissionError("denied")):
            _remove_empty_dir_if_present(str(dir_path))
