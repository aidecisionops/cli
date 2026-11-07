from __future__ import annotations

import argparse
import json
from typing import Any

from ..api_client import DopsClient
from ..argparse_utils import DopsHelpFormatter, add_examples
from ..git import resolve_repo_path
from ..ui import SelectOption, console, prompt_select, prompt_text, reset_flow_state
from .shared import decision_id


def _client_from_flags(flags: argparse.Namespace) -> DopsClient:
    return DopsClient.from_auth(resolve_repo_path(flags.repo_path) or None)


def _split_multi_values(raw_values: list[str] | None) -> list[str]:
    if not raw_values:
        return []
    items: list[str] = []
    for raw in raw_values:
        for part in str(raw).split(","):
            cleaned = part.strip()
            if cleaned:
                items.append(cleaned)
    return items


def _extract_section_field(decision: dict[str, Any], key: str) -> Any:
    direct = decision.get(key)
    if direct:
        return direct
    content = decision.get("content")
    if isinstance(content, dict):
        sections = content.get("extractedSections")
        if isinstance(sections, dict) and sections.get(key):
            return sections.get(key)
        metadata = content.get("metadata")
        if isinstance(metadata, dict):
            planner = metadata.get("decisionOpsPlanner")
            if isinstance(planner, dict) and planner.get(key):
                return planner.get(key)
    return None


def run_decisions_list(flags: argparse.Namespace) -> None:
    client = _client_from_flags(flags)
    decisions = client.list_decisions({"status": flags.status, "type": flags.type, "limit": flags.limit})
    if getattr(flags, "json", False):
        print(json.dumps({"decisions": decisions, "total": len(decisions)}, indent=2))
        return
    if not decisions:
        console.print("No decisions found.")
        return
    for decision in decisions:
        status = str(decision.get("status") or "–").ljust(12)
        decision_type = str(decision.get("type") or "–").ljust(12)
        title = str(decision.get("title") or "–")
        console.print(f"{decision_id(decision)}  {status}  {decision_type}  {title}")


def run_decisions_get(decision_id_value: str, flags: argparse.Namespace) -> None:
    client = _client_from_flags(flags)
    decision = client.get_decision(decision_id_value)
    if getattr(flags, "json", False):
        print(json.dumps(decision, indent=2))
        return
    console.print(f"ID:       {decision_id(decision)}")
    console.print(f"Title:    {decision.get('title')}")
    console.print(f"Status:   {decision.get('status')}")
    console.print(f"Type:     {decision.get('type')}")
    console.print(f"Version:  {decision.get('version')}")
    context_val = _extract_section_field(decision, "context")
    if context_val:
        console.print(f"Context:  {context_val}")
    outcome_val = decision.get("outcome") or _extract_section_field(decision, "decision")
    if outcome_val:
        console.print(f"Outcome:  {outcome_val}")
    options_val = _extract_section_field(decision, "options")
    if isinstance(options_val, list) and options_val:
        console.print("Options:")
        for option in options_val:
            if isinstance(option, dict):
                console.print(f"  - {option.get('name')}{': ' + option.get('description') if option.get('description') else ''}")
                if option.get("pros"):
                    console.print(f"    Pros: {', '.join(option['pros'])}")
                if option.get("cons"):
                    console.print(f"    Cons: {', '.join(option['cons'])}")
            else:
                console.print(f"  - {option}")
    consequences_val = _extract_section_field(decision, "consequences")
    if isinstance(consequences_val, list) and consequences_val:
        console.print("Consequences:")
        for consequence in consequences_val:
            console.print(f"  - {consequence}")
    if decision.get("createdAt"):
        console.print(f"Created:  {decision.get('createdAt')}")
    if decision.get("updatedAt"):
        console.print(f"Updated:  {decision.get('updatedAt')}")


def run_decisions_search(terms: str, flags: argparse.Namespace) -> None:
    query = (getattr(flags, "query", None) or terms or "").strip()
    if not query:
        raise RuntimeError("Search query is required. Pass search terms or --query.")
    client = _client_from_flags(flags)
    result = client.search_decisions(query, {"mode": flags.mode} if flags.mode else None)
    if getattr(flags, "json", False):
        print(json.dumps(result, indent=2))
        return
    decisions = result.get("decisions", []) if isinstance(result, dict) else []
    total = len(decisions)
    if isinstance(result, dict):
        try:
            total = int(result.get("total", len(decisions)))
        except (TypeError, ValueError):
            total = len(decisions)
    if not decisions:
        console.print("No matching decisions found.")
        return
    console.print(f"Found {total} result{'s' if total != 1 else ''}:")
    for decision in decisions:
        console.print(f"  {decision_id(decision)}  {str(decision.get('status', '')).ljust(12)}  {decision.get('title')}")


def run_decisions_create(flags: argparse.Namespace) -> None:
    reset_flow_state()
    client = _client_from_flags(flags)
    title = getattr(flags, "title", None)
    decision_type = getattr(flags, "type", None)
    context = getattr(flags, "context", None)

    if not title:
        if not console.is_terminal:
            raise RuntimeError("--title is required in non-interactive mode.")
        title = prompt_text(
            title="Decision title",
            placeholder="What decision are you recording?",
            validate=lambda value: None if value else "Title is required.",
        )
    if not decision_type:
        if getattr(flags, "title", None) or not console.is_terminal:
            decision_type = "technical"
        else:
            decision_type = prompt_select(
                "Decision type",
                [
                    SelectOption("Technical", "technical", "Architecture, tooling, infrastructure"),
                    SelectOption("Product", "product", "Features, UX, roadmap"),
                    SelectOption("Business", "business", "Strategy, process, organization"),
                    SelectOption("Governance", "governance", "Policies, standards, compliance"),
                ],
            )
    if context is None and not getattr(flags, "title", None) and console.is_terminal:
        context = prompt_text(title="Context (what prompted this decision?)", placeholder="Describe the situation...")

    options_list = _split_multi_values(getattr(flags, "options", None))
    consequences_list = _split_multi_values(getattr(flags, "consequences", None))
    related_list = _split_multi_values(getattr(flags, "related", None))
    decision_statement = getattr(flags, "decision", None)

    if client.org_id and client.project_id:
        scope_level = getattr(flags, "scope_level", None) or ("repo" if client.repo_ref else "project")
        draft_payload: dict[str, Any] = {
            "org_id": client.org_id,
            "project_id": client.project_id,
            "title": title,
            "type": decision_type,
            "context": context or title,
            "options": options_list or ["Selected architecture approach", "Status quo baseline"],
            "decision": decision_statement or context or title,
            "consequences": consequences_list or ["Requires implementation and governance tracking"],
            "related": related_list or [client.repo_ref or "repository"],
            "validation_plan": {
                "metric": getattr(flags, "validation_metric", None) or "implementation_conformance",
                "baseline": getattr(flags, "validation_baseline", None) or "unrecorded",
                "target": getattr(flags, "validation_target", None) or "verified_in_production",
                "by_date": getattr(flags, "validation_by_date", None) or "next-release",
            },
            "scope_level": scope_level,
        }
        if scope_level == "repo" and client.repo_ref:
            draft_payload["repo_id"] = client.repo_ref
        draft_result = client.create_draft(draft_payload)
        payload_out = draft_result.model_dump() if hasattr(draft_result, "model_dump") else draft_result
        if getattr(flags, "json", False):
            print(json.dumps(payload_out, indent=2))
            return
        created_id = payload_out.get("decision_id") if isinstance(payload_out, dict) else getattr(draft_result, "decision_id", "?")
        created_version = payload_out.get("version") if isinstance(payload_out, dict) else getattr(draft_result, "version", "?")
        console.print(f"Created decision: {created_id} (v{created_version})")
        return

    result = client.create_decision({"title": title, "type": decision_type, "context": context or None})
    if getattr(flags, "json", False):
        print(json.dumps(result, indent=2))
        return
    decision = result.get("decision", result) if isinstance(result, dict) else {}
    console.print(f"Created decision: {decision_id(decision)} (v{decision.get('version', '?')})")


def register_decision_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    decisions = subparsers.add_parser("decisions", formatter_class=DopsHelpFormatter, help="Work with decisions", description="Work with decisions")
    decisions.set_defaults(func=lambda args: decisions.print_help() or 0)
    decisions_subparsers = decisions.add_subparsers(dest="decisions_command")
    add_examples(decisions, ["dops decisions list", "dops decisions get dec_123", "dops decisions search auth onboarding", "dops decisions create"])

    decisions_list = decisions_subparsers.add_parser("list", formatter_class=DopsHelpFormatter, help="List decisions", description="List decisions")
    decisions_list.add_argument("--status", help="Filter decisions by lifecycle status (e.g. proposed, accepted, deprecated)")
    decisions_list.add_argument("--type", help="Filter decisions by category (technical, product, business, governance)")
    decisions_list.add_argument("--limit", type=int, default=20, help="Maximum number of decisions to return (default: 20)")
    decisions_list.add_argument("--repo-path", help="Path to the target repository")
    decisions_list.add_argument("--json", action="store_true", help="Output result as JSON")
    decisions_list.set_defaults(func=run_decisions_list)

    decisions_get = decisions_subparsers.add_parser("get", formatter_class=DopsHelpFormatter, help="Get a decision by ID", description="Get a decision by ID")
    decisions_get.add_argument("id", help="Decision identifier (e.g. dec_123)")
    decisions_get.add_argument("--repo-path", help="Path to the target repository")
    decisions_get.add_argument("--json", action="store_true", help="Output result as JSON")
    decisions_get.set_defaults(func=lambda args: run_decisions_get(args.id, args))

    decisions_search = decisions_subparsers.add_parser("search", formatter_class=DopsHelpFormatter, help="Search decisions by keywords", description="Search decisions by keywords")
    decisions_search.add_argument("terms", nargs="*", help="Search query keywords")
    decisions_search.add_argument("--query", dest="query", help="Search query string")
    decisions_search.add_argument("--mode", help="Search mode (e.g. hybrid, semantic, keyword)")
    decisions_search.add_argument("--repo-path", help="Path to the target repository")
    decisions_search.add_argument("--json", action="store_true", help="Output result as JSON")
    decisions_search.set_defaults(func=lambda args: run_decisions_search(" ".join(args.terms or []), args))

    decisions_create = decisions_subparsers.add_parser("create", formatter_class=DopsHelpFormatter, help="Create a new decision", description="Create a new decision")
    decisions_create.add_argument("--title", help="Decision title")
    decisions_create.add_argument("--type", choices=["technical", "product", "business", "governance"], help="Decision category (technical, product, business, governance)")
    decisions_create.add_argument("--context", help="Context explaining what prompted the decision")
    decisions_create.add_argument("--decision", help="Concrete decision outcome or chosen approach")
    decisions_create.add_argument("--options", action="append", help="Considered option(s) (repeatable or comma-separated)")
    decisions_create.add_argument("--consequences", action="append", help="Expected consequence(s) (repeatable or comma-separated)")
    decisions_create.add_argument("--related", action="append", help="Related component, repo, or decision reference(s)")
    decisions_create.add_argument("--scope-level", dest="scope_level", choices=["org", "project", "repo"], help="Governance scope level (default: repo)")
    decisions_create.add_argument("--validation-metric", dest="validation_metric", help="Validation metric name")
    decisions_create.add_argument("--validation-baseline", dest="validation_baseline", help="Validation metric baseline")
    decisions_create.add_argument("--validation-target", dest="validation_target", help="Validation metric target")
    decisions_create.add_argument("--validation-by-date", dest="validation_by_date", help="Validation target date or milestone")
    decisions_create.add_argument("--repo-path", help="Path to the target repository")
    decisions_create.add_argument("--json", action="store_true", help="Output result as JSON")
    decisions_create.set_defaults(func=run_decisions_create)
