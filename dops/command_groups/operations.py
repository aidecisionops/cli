from __future__ import annotations

import argparse
import json
from typing import Any

from ..api_client import DopsClient
from ..argparse_utils import DopsHelpFormatter, add_examples
from ..git import find_repo_root, git_changed_files, infer_repo_ref, resolve_repo_path
from ..runtime import emit_diagnostic
from ..ui import console, prompt_text, with_spinner
from .shared import require_project_binding


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_json_value(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _format_confidence(confidence: object) -> str | None:
    if confidence is None:
        return None
    try:
        return f"{round(float(confidence) * 100):.0f}%"
    except (TypeError, ValueError):
        emit_diagnostic(f"Decision gate returned non-numeric confidence: {confidence!r}")
        return None


def _parse_version(value: object, *, source: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise RuntimeError(f"{source} must be an integer.") from None


def _parse_changed_paths(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def run_gate(flags: argparse.Namespace) -> None:
    repo_path = resolve_repo_path(flags.repo_path) or None
    client = DopsClient.from_auth(repo_path)
    task_summary = flags.task or getattr(flags, "task_summary", None)
    if not task_summary:
        if not console.is_terminal:
            raise RuntimeError("--task is required in non-interactive mode.")
        task_summary = prompt_text(
            title="What task are you working on?",
            placeholder="Describe the task or change...",
            validate=lambda value: None if value else "Task summary is required.",
        )
    repo_ref = client.repo_ref
    if not repo_ref and repo_path:
        try:
            repo_ref = infer_repo_ref(repo_path)
        except RuntimeError:
            repo_ref = None
    if not repo_ref:
        raise RuntimeError("Could not determine repo_ref. Run `dops init` or pass --repo-path inside a configured repo.")
    explicit_paths = _parse_changed_paths(getattr(flags, "changed_paths", None))
    if explicit_paths:
        changed_paths = explicit_paths
    else:
        root = repo_path or find_repo_root() or None
        changed_paths = git_changed_files(root) if root else []
    result = with_spinner("Running decision gate...", lambda: client.prepare_gate(repo_ref, task_summary, changed_paths or None))
    if getattr(flags, "json", False):
        print(json.dumps(_to_json_value(result), indent=2))
        return
    console.print(f"Recordable:  {'yes' if _field(result, 'recordable') else 'no'}")
    confidence = _format_confidence(_field(result, "confidence"))
    if confidence is not None:
        console.print(f"Confidence:  {confidence}")
    reason = _field(result, "classification_reason") or _field(result, "reasoning")
    if reason:
        console.print(f"Reasoning:   {reason}")
    if _field(result, "suggested_mode"):
        console.print(f"Mode:        {_field(result, 'suggested_mode')}")


def run_validate(decision_id: str | None, flags: argparse.Namespace) -> None:
    repo_path = resolve_repo_path(flags.repo_path) or None
    client = DopsClient.from_auth(repo_path)
    org_id, project_id = require_project_binding(client)
    resolved_decision_id = decision_id or getattr(flags, "decision_id", None)
    payload = {"org_id": org_id, "project_id": project_id}
    if resolved_decision_id:
        payload["decision_id"] = resolved_decision_id
    result = with_spinner("Validating decision...", lambda: client.validate_decision(payload))
    if getattr(flags, "json", False):
        print(json.dumps(_to_json_value(result), indent=2))
        return
    console.print(f"Valid: {'yes' if _field(result, 'valid') else 'no'}")
    errors = _field(result, "errors") or []
    warnings = _field(result, "warnings") or []
    if errors:
        console.print("Errors:")
        for error in errors:
            console.print(f"  - {_field(error, 'message', error)}")
    if warnings:
        console.print("Warnings:")
        for warning in warnings:
            console.print(f"  - {_field(warning, 'message', warning)}")


def run_publish(decision_id: str | None, flags: argparse.Namespace) -> None:
    repo_path = resolve_repo_path(flags.repo_path) or None
    expected_version = flags.version
    resolved_decision_id = decision_id or getattr(flags, "decision_id", None)
    if not resolved_decision_id:
        raise RuntimeError("Decision ID is required. Pass positional <id> or --decision-id.")
    client = DopsClient.from_auth(repo_path)
    org_id, project_id = require_project_binding(client)
    if expected_version is None:
        decision = client.get_decision(resolved_decision_id)
        if decision.get("version") is None:
            raise RuntimeError("Could not determine current decision version. Pass --version.")
        try:
            expected_version = _parse_version(decision["version"], source="DecisionOps decision version")
        except RuntimeError as error:
            raise RuntimeError(f"{error} Pass --version explicitly.") from None
    result = with_spinner(
        "Publishing decision...",
        lambda: client.publish_decision(
            {"org_id": org_id, "project_id": project_id, "decision_id": resolved_decision_id, "expected_version": expected_version}
        ),
    )
    if getattr(flags, "json", False):
        print(json.dumps(_to_json_value(result), indent=2))
        return
    console.print(f"Published: {_field(result, 'decision_id', resolved_decision_id)} (v{_field(result, 'version', expected_version)})")


def run_status(flags: argparse.Namespace) -> None:
    repo_path = resolve_repo_path(flags.repo_path) or None
    client = DopsClient.from_auth(repo_path)
    snapshot, alerts = with_spinner("Loading governance data...", lambda: (client.get_monitoring_snapshot(), client.get_alerts()))
    if getattr(flags, "json", False):
        print(json.dumps({"snapshot": snapshot, "alerts": alerts}, indent=2))
        return
    total_decisions = snapshot.get("totalDecisions", snapshot.get("total_decisions", "n/a"))
    coverage = snapshot.get("coveragePercent", snapshot.get("coverage_percent", "n/a"))
    health = snapshot.get(
        "healthPercent",
        snapshot.get(
            "health_percent",
            snapshot.get("decisionHealthPercent", snapshot.get("decision_health_percent", "n/a")),
        ),
    )
    drift_rate = snapshot.get(
        "driftRate",
        snapshot.get(
            "drift_rate",
            snapshot.get("driftRatePerWeek", snapshot.get("drift_rate_per_week", "n/a")),
        ),
    )
    console.print("Governance Snapshot")
    console.print(f"  Total decisions: {total_decisions}")
    console.print(f"  Coverage:        {coverage}%")
    console.print(f"  Health:          {health}%")
    console.print(f"  Drift rate:      {drift_rate}")
    by_status = snapshot.get("byStatus") or snapshot.get("by_status") or {}
    if isinstance(by_status, dict) and by_status:
        console.print("  By status:")
        for status, count in by_status.items():
            console.print(f"    {status}: {count}")
    if alerts:
        console.print(f"\nAlerts ({len(alerts)}):")
        for alert in alerts:
            console.print(f"  [{alert.get('severity', 'info')}] {alert.get('message', '')}", markup=False)


def register_operation_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    gate = subparsers.add_parser("gate", formatter_class=DopsHelpFormatter, help="Run decision gate on current task", description="Run decision gate on current task")
    gate.add_argument("--task", help="Summary of the current task or change being evaluated")
    gate.add_argument("--task-summary", dest="task_summary", help="Summary of the current task or change being evaluated (alias for --task)")
    gate.add_argument("--changed-paths", dest="changed_paths", help="Comma-separated list of changed file paths")
    gate.add_argument("--repo-path", help="Path to the target repository")
    gate.add_argument("--json", action="store_true", help="Output result as JSON")
    gate.set_defaults(func=run_gate)
    add_examples(gate, ['dops gate --task "add oauth callback validation"'])

    validate = subparsers.add_parser("validate", formatter_class=DopsHelpFormatter, help="Validate a decision against org constraints", description="Validate a decision against org constraints")
    validate.add_argument("id", nargs="?", help="Optional decision ID to validate (validates project decisions when omitted)")
    validate.add_argument("--decision-id", dest="decision_id", help="Optional decision ID to validate")
    validate.add_argument("--repo-path", help="Path to the target repository")
    validate.add_argument("--json", action="store_true", help="Output result as JSON")
    validate.set_defaults(func=lambda args: run_validate(args.id, args))
    add_examples(validate, ["dops validate", "dops validate dec_123"])

    publish = subparsers.add_parser("publish", formatter_class=DopsHelpFormatter, help="Publish a proposed decision (transition to accepted)", description="Publish a proposed decision (transition to accepted)")
    publish.add_argument("id", nargs="?", help="Decision identifier to publish (e.g. dec_123)")
    publish.add_argument("--decision-id", dest="decision_id", help="Decision identifier to publish (e.g. dec_123)")
    publish.add_argument("--version", type=int, help="Expected current version of the decision for optimistic concurrency")
    publish.add_argument("--repo-path", help="Path to the target repository")
    publish.add_argument("--json", action="store_true", help="Output result as JSON")
    publish.set_defaults(func=lambda args: run_publish(args.id, args))
    add_examples(publish, ["dops publish dec_123", "dops publish dec_123 --version 7"])

    status = subparsers.add_parser("status", formatter_class=DopsHelpFormatter, help="Governance snapshot: coverage, health, drift, alerts", description="Governance snapshot: coverage, health, drift, alerts")
    status.add_argument("--repo-path", help="Path to the target repository")
    status.add_argument("--json", action="store_true", help="Output result as JSON")
    status.set_defaults(func=run_status)
    add_examples(status, ["dops status"])
