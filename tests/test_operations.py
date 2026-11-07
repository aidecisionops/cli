from __future__ import annotations

import argparse
import unittest
from unittest.mock import MagicMock, patch

from dops.command_groups.decisions import run_decisions_create, run_decisions_get
from dops.command_groups.operations import run_gate, run_publish, run_status


class OperationsTests(unittest.TestCase):
    def test_run_gate_skips_invalid_confidence_without_crashing(self) -> None:
        client = MagicMock()
        client.repo_ref = "acme/backend"
        client.prepare_gate.return_value = {"recordable": True, "confidence": "not-a-number"}
        flags = argparse.Namespace(repo_path=None, task="ship it")
        with patch("dops.command_groups.operations.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.operations.resolve_repo_path", return_value=None):
                with patch("dops.command_groups.operations.find_repo_root", return_value=None):
                    with patch("dops.command_groups.operations.with_spinner", side_effect=lambda _label, fn: fn()):
                        with patch("dops.command_groups.operations.emit_diagnostic") as emit:
                            with patch("dops.command_groups.operations.console.print") as print_mock:
                                run_gate(flags)
        emit.assert_called_once()
        rendered = [" ".join(str(arg) for arg in call.args) for call in print_mock.call_args_list]
        self.assertTrue(any("Recordable:" in line for line in rendered))
        self.assertFalse(any("Confidence:" in line for line in rendered))

    def test_run_publish_uses_valid_cli_version(self) -> None:
        client = MagicMock(org_id="org_123", project_id="proj_456")
        client.publish_decision.return_value = {"decision_id": "dec_123", "version": 7}
        flags = argparse.Namespace(repo_path=None, version=7)
        with patch("dops.command_groups.operations.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.operations.resolve_repo_path", return_value=None):
                with patch("dops.command_groups.operations.console.print"):
                    run_publish("dec_123", flags)
        client.get_decision.assert_not_called()
        client.publish_decision.assert_called_once()

    def test_run_publish_rejects_invalid_server_version(self) -> None:
        client = MagicMock(org_id="org_123", project_id="proj_456")
        client.get_decision.return_value = {"version": "abc"}
        flags = argparse.Namespace(repo_path=None, version=None)
        with patch("dops.command_groups.operations.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.operations.resolve_repo_path", return_value=None):
                with self.assertRaises(RuntimeError) as raised:
                    run_publish("dec_123", flags)
        self.assertIn("DecisionOps decision version must be an integer", str(raised.exception))

    def test_run_status_reads_decision_health_and_drift_rate_per_week(self) -> None:
        client = MagicMock()
        client.get_monitoring_snapshot.return_value = {
            "coveragePercent": 92,
            "decisionHealthPercent": 88,
            "driftRatePerWeek": 1,
        }
        client.get_alerts.return_value = []
        flags = argparse.Namespace(repo_path=None, json=False)
        with patch("dops.command_groups.operations.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.operations.resolve_repo_path", return_value=None):
                with patch("dops.command_groups.operations.with_spinner", side_effect=lambda _label, fn: fn()):
                    with patch("dops.command_groups.operations.console.print") as print_mock:
                        run_status(flags)
        rendered = [" ".join(str(arg) for arg in call.args) for call in print_mock.call_args_list]
        self.assertTrue(any("Coverage:        92%" in line for line in rendered))
        self.assertTrue(any("Health:          88%" in line for line in rendered))
        self.assertTrue(any("Drift rate:      1" in line for line in rendered))

    def test_run_decisions_create_uses_create_draft_with_repo_id_when_bound(self) -> None:
        client = MagicMock(org_id="org_1", project_id="proj_1", repo_ref="acme/backend")
        client.create_draft.return_value = {"decision_id": "DEC-2026-0001", "version": 1, "status": "Proposed"}
        flags = argparse.Namespace(
            repo_path=None,
            title="Use RS256 JWTs",
            type="technical",
            context="Stateless edge verification",
            decision="Adopt RS256",
            options=["RS256,HS256"],
            consequences=["Requires JWKS endpoint"],
            related=None,
            scope_level=None,
            validation_metric=None,
            validation_baseline=None,
            validation_target=None,
            validation_by_date=None,
            json=False,
        )
        with patch("dops.command_groups.decisions.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.decisions.resolve_repo_path", return_value=None):
                with patch("dops.command_groups.decisions.console.print") as print_mock:
                    run_decisions_create(flags)
        client.create_draft.assert_called_once()
        payload = client.create_draft.call_args.args[0]
        self.assertEqual(payload["repo_id"], "acme/backend")
        self.assertEqual(payload["options"], ["RS256", "HS256"])
        print_mock.assert_called_once_with("Created decision: DEC-2026-0001 (v1)")

    def test_run_decisions_get_extracts_nested_content_sections(self) -> None:
        client = MagicMock()
        client.get_decision.return_value = {
            "decisionId": "DEC-2026-0001",
            "title": "Use RS256 JWTs",
            "status": "accepted",
            "type": "technical",
            "version": 2,
            "content": {
                "extractedSections": {
                    "context": "Edge workers need stateless verification",
                    "decision": "Verify JWTs using JWKS",
                    "options": ["RS256", "Opaque tokens"],
                }
            },
        }
        flags = argparse.Namespace(repo_path=None, json=False)
        with patch("dops.command_groups.decisions.DopsClient.from_auth", return_value=client):
            with patch("dops.command_groups.decisions.resolve_repo_path", return_value=None):
                with patch("dops.command_groups.decisions.console.print") as print_mock:
                    run_decisions_get("DEC-2026-0001", flags)
        rendered = [" ".join(str(arg) for arg in call.args) for call in print_mock.call_args_list]
        self.assertTrue(any("Context:  Edge workers need stateless verification" in line for line in rendered))
        self.assertTrue(any("Outcome:  Verify JWTs using JWKS" in line for line in rendered))
        self.assertTrue(any("- RS256" in line for line in rendered))
