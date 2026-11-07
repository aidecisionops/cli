from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dops.command_groups.repo import _CREATE_PROJECT, _KEEP_EXISTING_BINDING, _MANUAL_SELECTION, _doctor_platform_issue, _existing_binding_access_summary, _organization_options, _pick_option, _project_options, _resolve_binding_from_workspace_context, _verify_or_attach_project_repository, run_doctor, run_init
from dops.mcp_inspect import ApiAuthProbeResult, McpReachabilityResult
from dops.ui import SelectOption


class RepoCommandTests(unittest.TestCase):
    def test_doctor_platform_issue_translates_missing_bundle_error(self) -> None:
        message = _doctor_platform_issue(
            RuntimeError("Could not find platform definitions. Ensure @decisionops/skill is installed or is adjacent.")
        )
        self.assertIn("dops install", message)
        self.assertIn("running `dops` from source", message)
        self.assertNotIn("@decisionops/skill", message)

    def test_organization_options_include_name_and_id(self) -> None:
        options = _organization_options(
            {
                "activeOrganization": {"orgId": "org_123", "orgName": "Acme", "role": "admin"},
                "organizations": [{"orgId": "org_123", "orgName": "Acme", "role": "admin"}],
            }
        )
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].value, "org_123")
        self.assertIn("Acme", options[0].label)

    def test_project_options_filter_by_org(self) -> None:
        options = _project_options(
            {
                "projects": [
                    {"id": "proj_123", "name": "Backend", "projectKey": "backend", "orgId": "org_123"},
                    {"id": "proj_999", "name": "Other", "projectKey": "other", "orgId": "org_999"},
                ]
            },
            "org_123",
        )
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].value, "proj_123")
        self.assertIn("Backend", options[0].label)

    def test_run_init_uses_workspace_selection_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            flags = argparse.Namespace(
                repo_path=str(repo_path),
                api_base_url=None,
                org_id=None,
                project_id=None,
                repo_ref="acme/backend",
                repo_id=None,
                default_branch="main",
                user_session_token=None,
                allow_placeholders=False,
                server_name=None,
                server_url=None,
            )
            with patch("dops.command_groups.repo.is_interactive", return_value=True):
                with patch(
                    "dops.command_groups.repo._resolve_binding_from_workspace_context",
                    return_value=("org_123", "proj_123"),
                ):
                    with patch("dops.command_groups.repo._verify_or_attach_project_repository", return_value=("ok", "linked")):
                        with patch("dops.command_groups.repo.prompt_text") as prompt_text:
                            with patch("dops.command_groups.repo.console.print"):
                                run_init(flags)
            prompt_text.assert_not_called()
            manifest = (repo_path / ".decisionops" / "manifest.toml").read_text(encoding="utf8")
            self.assertIn('org_id = "org_123"', manifest)
            self.assertIn('project_id = "proj_123"', manifest)

    def test_pick_option_offers_manual_path_for_single_option(self) -> None:
        with patch("dops.command_groups.repo.prompt_select", return_value=_MANUAL_SELECTION) as prompt_select:
            result = _pick_option(
                "Choose project",
                [SelectOption(label="Default Project (default)", value="proj_default", description="Project id: proj_default • default")],
                description="Select the DecisionOps project that should govern this repository.",
                create_label="Create a new project",
                create_value=_CREATE_PROJECT,
                manual_label="Enter a different project_id",
            )
        self.assertEqual(result, _MANUAL_SELECTION)
        options = prompt_select.call_args.args[1]
        self.assertEqual(options[0].label, "Use Default Project (default)")
        self.assertEqual(options[1].label, "Create a new project")
        self.assertEqual(options[2].label, "Enter a different project_id")

    def test_resolve_binding_can_create_project_from_cli(self) -> None:
        context = {
            "activeOrganization": {"orgId": "org_123", "orgName": "Acme", "role": "admin"},
            "organizations": [{"orgId": "org_123", "orgName": "Acme", "role": "admin"}],
            "activeProject": {"id": "org_123:default", "name": "Default Project", "projectKey": "default", "orgId": "org_123", "isDefault": True},
            "projects": [{"id": "org_123:default", "name": "Default Project", "projectKey": "default", "orgId": "org_123", "isDefault": True}],
        }
        refreshed = {
            **context,
            "activeProject": {"id": "proj_456", "name": "Payments Platform", "projectKey": "payments-platform", "orgId": "org_123", "isDefault": False},
            "projects": [
                context["projects"][0],
                {"id": "proj_456", "name": "Payments Platform", "projectKey": "payments-platform", "orgId": "org_123", "isDefault": False},
            ],
        }
        with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
            with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=type("Auth", (), {"accessToken": "dop_token", "apiBaseUrl": "https://api.example.com"})()):
                with patch("dops.command_groups.repo.load_session_context", side_effect=[context, refreshed]):
                    with patch("dops.command_groups.repo.prompt_select", side_effect=["org_123", _CREATE_PROJECT]):
                        with patch("dops.command_groups.repo.prompt_text", return_value="Payments Platform"):
                            with patch("dops.command_groups.repo.prompt_confirm", return_value=False):
                                with patch("dops.command_groups.repo.with_spinner", side_effect=lambda _label, fn: fn()):
                                    with patch("dops.command_groups.repo.DopsClient") as client_cls:
                                        client = client_cls.return_value
                                        client.api_base_url = "https://api.example.com"
                                        client.create_project.return_value = {"id": "proj_456", "name": "Payments Platform"}
                                        resolved_org_id, resolved_project_id = _resolve_binding_from_workspace_context(
                                            org_id=None,
                                            project_id=None,
                                            api_base_url=None,
                                        )
        self.assertEqual(resolved_org_id, "org_123")
        self.assertEqual(resolved_project_id, "proj_456")
        client.create_project.assert_called_once_with("Payments Platform", set_default=False)
        client.switch_active_project.assert_called_once_with("proj_456")

    def test_run_init_keeps_existing_manifest_when_user_chooses_keep(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = decisionops_dir / "manifest.toml"
            original_manifest = '\n'.join(
                [
                    'version = 1',
                    'org_id = "org_existing"',
                    'project_id = "proj_existing"',
                    'repo_ref = "acme/existing"',
                    'default_branch = "main"',
                    'mcp_server_name = "decision-ops-mcp"',
                    'mcp_server_url = "https://api.aidecisionops.com/mcp"',
                    '',
                ]
            )
            manifest_path.write_text(original_manifest, encoding="utf8")
            flags = argparse.Namespace(
                repo_path=str(repo_path),
                api_base_url=None,
                org_id="org_new",
                project_id="proj_new",
                repo_ref="acme/new",
                repo_id=None,
                default_branch="main",
                user_session_token=None,
                allow_placeholders=False,
                server_name=None,
                server_url=None,
            )
            with patch("dops.command_groups.repo.is_interactive", return_value=True):
                with patch("dops.command_groups.repo.prompt_select", return_value=_KEEP_EXISTING_BINDING):
                    with patch("dops.command_groups.repo._verify_or_attach_project_repository", return_value=("ok", "linked")):
                        with patch("dops.command_groups.repo.console.print"):
                            run_init(flags)
            self.assertEqual(manifest_path.read_text(encoding="utf8"), original_manifest)

    def test_run_init_refuses_to_overwrite_manifest_non_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            (decisionops_dir / "manifest.toml").write_text(
                '\n'.join(
                    [
                        'version = 1',
                        'org_id = "org_existing"',
                        'project_id = "proj_existing"',
                        'repo_ref = "acme/existing"',
                        'default_branch = "main"',
                        'mcp_server_name = "decision-ops-mcp"',
                        'mcp_server_url = "https://api.aidecisionops.com/mcp"',
                        '',
                    ]
                ),
                encoding="utf8",
            )
            flags = argparse.Namespace(
                repo_path=str(repo_path),
                api_base_url=None,
                org_id="org_new",
                project_id="proj_new",
                repo_ref="acme/new",
                repo_id=None,
                default_branch="main",
                user_session_token=None,
                allow_placeholders=False,
                server_name=None,
                server_url=None,
            )
            with patch("dops.command_groups.repo.is_interactive", return_value=False):
                with self.assertRaises(RuntimeError) as raised:
                    run_init(flags)
            self.assertIn("already has .decisionops/manifest.toml", str(raised.exception))

    def test_existing_binding_access_summary_reports_accessible_binding(self) -> None:
        context = {
            "activeOrganization": {"orgId": "org_123", "orgName": "Acme", "role": "admin"},
            "organizations": [{"orgId": "org_123", "orgName": "Acme", "role": "admin"}],
            "activeProject": {"id": "proj_123", "name": "Backend", "projectKey": "backend", "orgId": "org_123"},
            "projects": [{"id": "proj_123", "name": "Backend", "projectKey": "backend", "orgId": "org_123"}],
        }
        with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
            with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=type("Auth", (), {"accessToken": "dop_token", "apiBaseUrl": "https://api.example.com"})()):
                with patch("dops.command_groups.repo.load_session_context", return_value=context):
                    state, lines = _existing_binding_access_summary(
                        {"org_id": "org_123", "project_id": "proj_123", "repo_ref": "acme/backend"}
                    )
        self.assertEqual(state, "accessible")
        self.assertIn("can access", lines[0])

    def test_existing_binding_access_summary_reports_inaccessible_project(self) -> None:
        context = {
            "activeOrganization": {"orgId": "org_123", "orgName": "Acme", "role": "admin"},
            "organizations": [{"orgId": "org_123", "orgName": "Acme", "role": "admin"}],
            "activeProject": {"id": "proj_default", "name": "Default Project", "projectKey": "default", "orgId": "org_123"},
            "projects": [{"id": "proj_default", "name": "Default Project", "projectKey": "default", "orgId": "org_123"}],
        }
        with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
            with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=type("Auth", (), {"accessToken": "dop_token", "apiBaseUrl": "https://api.example.com"})()):
                with patch("dops.command_groups.repo.load_session_context", return_value=context):
                    state, lines = _existing_binding_access_summary(
                        {"org_id": "org_123", "project_id": "proj_missing", "repo_ref": "acme/backend"}
                    )
        self.assertEqual(state, "inaccessible")
        self.assertIn("cannot access project_id", lines[0])

    def test_existing_binding_access_summary_does_not_switch_workspace_for_cross_org_validation(self) -> None:
        context = {
            "activeOrganization": {"orgId": "org_active", "orgName": "Active Org", "role": "admin"},
            "organizations": [
                {"orgId": "org_active", "orgName": "Active Org", "role": "admin"},
                {"orgId": "org_other", "orgName": "Other Org", "role": "admin"},
            ],
            "activeProject": {"id": "proj_active", "name": "Active Project", "projectKey": "active", "orgId": "org_active"},
            "projects": [{"id": "proj_active", "name": "Active Project", "projectKey": "active", "orgId": "org_active"}],
        }
        with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
            with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=type("Auth", (), {"accessToken": "dop_token", "apiBaseUrl": "https://api.example.com"})()):
                with patch("dops.command_groups.repo.load_session_context", return_value=context):
                    with patch("dops.command_groups.repo.DopsClient") as client_cls:
                        state, lines = _existing_binding_access_summary(
                            {"org_id": "org_other", "project_id": "proj_other", "repo_ref": "acme/backend"}
                        )
        self.assertEqual(state, "unknown")
        self.assertIn("could not be verified without switching", lines[0])
        client_cls.return_value.switch_active_org.assert_not_called()
        client_cls.return_value.switch_active_project.assert_not_called()

    def test_run_init_allows_repairing_invalid_manifest_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            (decisionops_dir / "manifest.toml").write_text("version = [\n", encoding="utf8")
            flags = argparse.Namespace(
                repo_path=str(repo_path),
                api_base_url=None,
                org_id="org_new",
                project_id="proj_new",
                repo_ref="acme/new",
                repo_id=None,
                default_branch="main",
                user_session_token=None,
                allow_placeholders=False,
                server_name=None,
                server_url=None,
            )
            with patch("dops.command_groups.repo.is_interactive", return_value=True):
                with patch("dops.command_groups.repo.prompt_select", return_value="_update_existing_binding_ignored"):
                    with patch("dops.command_groups.repo._verify_or_attach_project_repository", return_value=("ok", "linked")):
                        with patch("dops.command_groups.repo.console.print"):
                            run_init(flags)
            manifest = (decisionops_dir / "manifest.toml").read_text(encoding="utf8")
            self.assertIn('org_id = "org_new"', manifest)
            self.assertIn('project_id = "proj_new"', manifest)

    def test_run_init_refuses_invalid_manifest_non_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            (decisionops_dir / "manifest.toml").write_text("version = [\n", encoding="utf8")
            flags = argparse.Namespace(
                repo_path=str(repo_path),
                api_base_url=None,
                org_id="org_new",
                project_id="proj_new",
                repo_ref="acme/new",
                repo_id=None,
                default_branch="main",
                user_session_token=None,
                allow_placeholders=False,
                server_name=None,
                server_url=None,
            )
            with patch("dops.command_groups.repo.is_interactive", return_value=False):
                with self.assertRaises(RuntimeError) as raised:
                    run_init(flags)
            self.assertIn("Repository manifest is invalid", str(raised.exception))
            self.assertIn("dops init", str(raised.exception))

    def test_verify_or_attach_project_repository_attaches_missing_repo(self) -> None:
        with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
            with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=type("Auth", (), {"accessToken": "dop_token", "apiBaseUrl": "https://api.example.com"})()):
                with patch("dops.command_groups.repo.DopsClient") as client_cls:
                    client = client_cls.return_value
                    client.load_project_repositories.return_value = {"repositories": ["acme/other"]}
                    status, message = _verify_or_attach_project_repository(
                        org_id="org_live",
                        project_id="proj_live",
                        repo_ref="acme/backend",
                        attach_missing=True,
                    )
        self.assertEqual(status, "ok")
        self.assertIn("Linked `acme/backend`", message)
        client.attach_repository_to_project.assert_called_once_with("proj_live", "acme/backend")

    def test_verify_or_attach_project_repository_warns_when_auth_missing(self) -> None:
        with patch("dops.command_groups.repo.read_auth_state", return_value=None):
            status, message = _verify_or_attach_project_repository(
                org_id="org_live",
                project_id="proj_live",
                repo_ref="acme/backend",
                attach_missing=True,
            )
        self.assertEqual(status, "warning")
        self.assertIn("Run `dops login`", message)

    def test_run_doctor_reports_missing_central_repository_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            (decisionops_dir / "manifest.toml").write_text(
                '\n'.join(
                    [
                        'version = 1',
                        'org_id = "org_123"',
                        'project_id = "proj_123"',
                        'repo_ref = "acme/backend"',
                        'default_branch = "main"',
                        'mcp_server_name = "decision-ops-mcp"',
                        'mcp_server_url = "https://api.aidecisionops.com/mcp"',
                        '',
                    ]
                ),
                encoding="utf8",
            )
            flags = argparse.Namespace(repo_path=str(repo_path))
            auth = type(
                "Auth",
                (),
                {
                    "apiBaseUrl": "https://api.example.com",
                    "accessToken": "dop_token",
                    "user": {"email": "dev@example.com"},
                    "expiresAt": None,
                    "method": "token",
                },
            )()
            with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
                with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=auth):
                    with patch("dops.command_groups.repo.find_platforms_dir", return_value=Path(temp_dir)):
                        with patch("dops.command_groups.repo.load_platforms", return_value={}):
                            with patch(
                                "dops.command_groups.repo._verify_or_attach_project_repository",
                                return_value=(
                                    "error",
                                    "Project `proj_123` has no linked repositories in DecisionOps. Project-scoped decisions can still be recorded, but repo-scoped drafts and repo_ref-based resolution require a linked repository. Link `acme/backend` to this project to enable repository-scoped decisions for this repo.",
                                ),
                            ):
                                with patch(
                                    "dops.command_groups.repo.probe_api_auth",
                                    return_value=ApiAuthProbeResult(status=200),
                                ):
                                    with patch(
                                        "dops.command_groups.repo.probe_mcp_reachability",
                                        return_value=McpReachabilityResult(status=401),
                                    ):
                                        with patch("dops.command_groups.repo.render_doctor_report") as render_doctor_report:
                                            run_doctor(flags)
            issues = render_doctor_report.call_args.kwargs["issues"]
            self.assertIn(
                "Project `proj_123` has no linked repositories in DecisionOps. Project-scoped decisions can still be recorded, but repo-scoped drafts and repo_ref-based resolution require a linked repository. Link `acme/backend` to this project to enable repository-scoped decisions for this repo.",
                issues,
            )

    def test_run_doctor_reports_invalid_manifest_in_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            decisionops_dir = repo_path / ".decisionops"
            decisionops_dir.mkdir(parents=True, exist_ok=True)
            (decisionops_dir / "manifest.toml").write_text("version = [\n", encoding="utf8")
            flags = argparse.Namespace(repo_path=str(repo_path))
            auth = type(
                "Auth",
                (),
                {
                    "apiBaseUrl": "https://api.example.com",
                    "accessToken": "dop_token",
                    "user": {"email": "dev@example.com"},
                    "expiresAt": None,
                    "method": "token",
                },
            )()
            with patch("dops.command_groups.repo.read_auth_state", return_value=object()):
                with patch("dops.command_groups.repo.ensure_valid_auth_state", return_value=auth):
                    with patch("dops.command_groups.repo.find_platforms_dir", return_value=Path(temp_dir)):
                        with patch("dops.command_groups.repo.load_platforms", return_value={}):
                            with patch("dops.command_groups.repo.render_doctor_report") as render_doctor_report:
                                run_doctor(flags)
            issues = render_doctor_report.call_args.kwargs["issues"]
            self.assertTrue(any("Repository manifest is invalid" in issue for issue in issues))
