from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..api_client import DecisionOpsApiError, DopsClient
from ..argparse_utils import DopsHelpFormatter, add_examples, add_notes
from ..auth import clear_auth_state, ensure_valid_auth_state, read_auth_state, revoke_auth_state
from ..config import (
    DEFAULT_MCP_SERVER_NAME,
    DEFAULT_MCP_SERVER_URL,
    PLACEHOLDER_ORG_ID,
    PLACEHOLDER_PROJECT_ID,
    PLACEHOLDER_REPO_REF,
    config_error,
    config_path,
    expand_home,
)
from ..auth_trigger import (
    describe_trigger,
    execute_cli_trigger,
    platform_triggers,
    triggers_by_reason,
)
from ..mcp_inspect import (
    ApiAuthProbeResult,
    McpEntryReport,
    McpGateProbeResult,
    McpReachabilityResult,
    inspect_mcp_entry,
    probe_api_auth,
    probe_mcp_gate_resolution,
    probe_mcp_reachability,
)
from ..git import infer_default_branch, resolve_repo_path
from ..installer import cleanup_platforms, install_platforms
from ..manifest import InvalidManifestError, read_manifest, write_manifest
from ..platforms import load_platforms, resolve_install_path
from ..resources import find_platforms_dir, find_skill_source_dir, resolve_local_skill_repo
from ..tls import describe_tls_setup
from ..ui import PromptChrome, SelectOption, console, flow_chrome, prompt_confirm, prompt_select, prompt_text, render_cleanup_summary, render_doctor_report, render_install_summary, reset_flow_state, with_spinner
from .shared import (
    auth_display,
    choose_platforms,
    detect_repo_ref,
    is_interactive,
    load_session_context,
    normalize_repo_ref,
    project_repository_link_issue,
    project_repository_refs,
    resolve_server_name,
    resolve_server_url,
)

_MANUAL_SELECTION = "__manual__"
_CREATE_ORGANIZATION = "__create_organization__"
_CREATE_PROJECT = "__create_project__"
_KEEP_EXISTING_BINDING = "__keep_existing_binding__"
_UPDATE_EXISTING_BINDING = "__update_existing_binding__"
_CANCEL_EXISTING_BINDING = "__cancel_existing_binding__"


def _binding_uses_placeholders(org_id: str, project_id: str, repo_ref: str) -> bool:
    return org_id == PLACEHOLDER_ORG_ID or project_id == PLACEHOLDER_PROJECT_ID or repo_ref == PLACEHOLDER_REPO_REF


def _verify_or_attach_project_repository(
    *,
    org_id: str,
    project_id: str,
    repo_ref: str,
    api_base_url: str | None = None,
    attach_missing: bool,
) -> tuple[str, str]:
    if _binding_uses_placeholders(org_id, project_id, repo_ref):
        return ("ok", "Skipping central repository linking because the binding uses placeholder values.")
    try:
        current_auth = read_auth_state()
    except RuntimeError as error:
        return ("warning", f"Could not verify the central repository link: {error}")
    if current_auth is None:
        message = "CLI auth not configured, so the central project repository link could not be verified."
        return (
            "warning",
            f"{message} Run `dops login` and re-run `dops init`, or link `{repo_ref}` to project `{project_id}` in DecisionOps.",
        )
    try:
        auth = ensure_valid_auth_state(current_auth)
    except RuntimeError as error:
        return ("warning", f"Could not verify the central repository link: {error}")

    client = DopsClient(api_base_url=(api_base_url or auth.apiBaseUrl).rstrip("/"), token=auth.accessToken, org_id=org_id)
    try:
        repositories = project_repository_refs(client.load_project_repositories(project_id))
    except DecisionOpsApiError as error:
        return ("error", f"Could not load project repositories for `{project_id}`: {error}")

    if repo_ref in repositories:
        return ("ok", f"Verified central repository link for `{repo_ref}` in project `{project_id}`.")

    if not attach_missing:
        issue = project_repository_link_issue(project_id, repo_ref, repositories)
        return (
            "error",
            issue or f"Project `{project_id}` is not linked to `{repo_ref}` in DecisionOps.",
        )

    try:
        client.attach_repository_to_project(project_id, repo_ref)
    except DecisionOpsApiError as error:
        return ("error", f"Could not attach `{repo_ref}` to project `{project_id}`: {error}")
    return ("ok", f"Linked `{repo_ref}` to project `{project_id}` in DecisionOps.")


def _doctor_platform_issue(error: Exception) -> str:
    message = str(error)
    if "Could not find platform definitions" in message or "No platform definitions found" in message:
        return (
            "Could not inspect platform installs because the DecisionOps skill bundle is unavailable. "
            "Typical setup is `dops install`, then choose Codex, Cursor, Claude Code, or VS Code. "
            "If you are running `dops` from source, make sure the companion skill bundle is installed or adjacent to this checkout."
        )
    return f"Could not load platform definitions: {error}"


def _organization_options(context: dict[str, Any] | None) -> list[SelectOption[str]]:
    options: list[SelectOption[str]] = []
    seen: set[str] = set()
    for organization in ([context.get("activeOrganization")] if context and context.get("activeOrganization") else []) + list((context or {}).get("organizations") or []):
        org_id = str((organization or {}).get("orgId") or "").strip()
        if not org_id or org_id in seen:
            continue
        seen.add(org_id)
        org_name = str((organization or {}).get("orgName") or org_id).strip() or org_id
        role = str((organization or {}).get("role") or "").strip()
        label = org_name if org_name == org_id else f"{org_name} ({org_id})"
        options.append(SelectOption(label=label, value=org_id, description=(f"Role: {role}" if role else None)))
    return options


def _project_options(context: dict[str, Any] | None, org_id: str) -> list[SelectOption[str]]:
    options: list[SelectOption[str]] = []
    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    if context and context.get("activeProject"):
        projects.append(context["activeProject"])
    projects.extend(list((context or {}).get("projects") or []))
    for project in projects:
        project_org_id = str(project.get("orgId") or "").strip()
        project_id = str(project.get("id") or project.get("projectId") or "").strip()
        if project_org_id != org_id or not project_id or project_id in seen:
            continue
        seen.add(project_id)
        project_name = str(project.get("name") or project.get("projectName") or project.get("projectKey") or project_id).strip() or project_id
        project_key = str(project.get("projectKey") or "").strip()
        label = project_name if not project_key or project_key == project_name else f"{project_name} ({project_key})"
        description = f"Project id: {project_id}"
        if project.get("isDefault"):
            description += " • default"
        options.append(SelectOption(label=label, value=project_id, description=description))
    return options


def _pick_option(
    title: str,
    options: list[SelectOption[str]],
    *,
    description: str,
    create_label: str,
    create_value: str,
    manual_label: str,
) -> str:
    prompt_options: list[SelectOption[str]]
    if len(options) == 1:
        prompt_options = [
            SelectOption(
                label=f"Use {options[0].label}",
                value=options[0].value,
                description=options[0].description,
            ),
            SelectOption(
                label=create_label,
                value=create_value,
                description="Create a new resource from the CLI instead of using the existing default.",
            ),
            SelectOption(
                label=manual_label,
                value=_MANUAL_SELECTION,
                description="Use this if you already know the exact id you want to bind.",
            ),
        ]
    else:
        prompt_options = [
            *options,
            SelectOption(
                label=create_label,
                value=create_value,
                description="Create a new resource from the CLI.",
            ),
            SelectOption(
                label=manual_label,
                value=_MANUAL_SELECTION,
                description="Use this if you already know the exact id you want to bind.",
            ),
        ]
    return prompt_select(
        title,
        prompt_options,
        flow_chrome(PromptChrome(description=description)),
    )


def _organization_id(organization: dict[str, Any] | None) -> str | None:
    org_id = str((organization or {}).get("orgId") or (organization or {}).get("id") or "").strip()
    return org_id or None


def _project_id(project: dict[str, Any] | None) -> str | None:
    project_id = str((project or {}).get("id") or (project or {}).get("projectId") or "").strip()
    return project_id or None


def _projects_in_context(context: dict[str, Any] | None, org_id: str) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    if context and context.get("activeProject"):
        projects.append(context["activeProject"])
    projects.extend(list((context or {}).get("projects") or []))
    return [project for project in projects if str(project.get("orgId") or "").strip() == org_id]


def _existing_binding_access_summary(manifest: dict[str, Any], api_base_url: str | None = None) -> tuple[str, list[str]]:
    org_id = str(manifest.get("org_id") or "").strip()
    project_id = str(manifest.get("project_id") or "").strip()
    if not org_id or not project_id:
        return ("unknown", ["Binding check: manifest is missing org_id or project_id."])
    try:
        current_auth = read_auth_state()
    except RuntimeError as error:
        return ("unknown", [f"Binding check: {error}"])
    if current_auth is None:
        return ("unknown", ["Binding check: run `dops login` to verify access to the existing binding."])
    try:
        auth = ensure_valid_auth_state(current_auth)
    except RuntimeError as error:
        return ("unknown", [f"Binding check: {error}"])
    context = load_session_context(auth.accessToken, (api_base_url or auth.apiBaseUrl).rstrip("/"))
    if not context:
        return ("unknown", ["Binding check: could not load the current DecisionOps workspace context."])

    original_org_id = _organization_id(context.get("activeOrganization") if isinstance(context, dict) else None)
    accessible_org_ids = {_organization_id(org) for org in [context.get("activeOrganization"), *((context.get("organizations") or []))]}
    accessible_org_ids.discard(None)
    if org_id not in accessible_org_ids:
        return ("inaccessible", [f"Binding check: current auth cannot access org_id `{org_id}`."])
    if org_id != original_org_id:
        return (
            "unknown",
            [
                f"Binding check: current auth can access org_id `{org_id}`, but project access could not be verified without switching the active workspace.",
            ],
        )

    available_project_ids = {_project_id(project) for project in _projects_in_context(context, org_id)}
    available_project_ids.discard(None)
    if project_id not in available_project_ids:
        return ("inaccessible", [f"Binding check: current auth cannot access project_id `{project_id}` in org `{org_id}`."])
    return ("accessible", ["Binding check: current auth can access this existing org/project binding."])


def _manifest_file_path(repo_path: str) -> Path:
    return Path(repo_path) / ".decisionops" / "manifest.toml"


def _load_existing_manifest(repo_path: str) -> tuple[dict[str, Any] | None, str | None, bool]:
    manifest_path = _manifest_file_path(repo_path)
    if not manifest_path.exists():
        return None, None, False
    try:
        return read_manifest(repo_path), None, True
    except InvalidManifestError as error:
        return None, str(error), True


def _confirm_rebinding_for_invalid_manifest(repo_path: str, manifest_error: str) -> bool:
    choice = prompt_select(
        "This repository has an unreadable binding manifest",
        [
            SelectOption(
                label="Replace manifest with a new binding",
                value=_UPDATE_EXISTING_BINDING,
                description="Write a fresh manifest.toml and continue with a new binding.",
            ),
            SelectOption(
                label="Cancel",
                value=_CANCEL_EXISTING_BINDING,
                description="Abort without touching the existing manifest file.",
            ),
        ],
        flow_chrome(
            PromptChrome(
                description="\n".join(
                    [
                        f"Repository: {repo_path}",
                        manifest_error,
                    ]
                )
            )
        ),
    )
    if choice == _CANCEL_EXISTING_BINDING:
        raise RuntimeError("Cancelled.")
    return True


def _confirm_rebinding(repo_path: str, manifest: dict[str, Any], api_base_url: str | None = None) -> bool:
    access_state, access_lines = _existing_binding_access_summary(manifest, api_base_url)
    state_label = {
        "accessible": "Current auth status: accessible",
        "inaccessible": "Current auth status: inaccessible",
        "unknown": "Current auth status: could not verify",
    }[access_state]
    description = "\n".join(
        [
            f"Repository: {repo_path}",
            f"Current org_id: {manifest.get('org_id', '(missing)')}",
            f"Current project_id: {manifest.get('project_id', '(missing)')}",
            f"Current repo_ref: {manifest.get('repo_ref', '(missing)')}",
            state_label,
            *access_lines,
        ]
    )
    choice = prompt_select(
        "This repository is already bound",
        [
            SelectOption(
                label="Keep existing binding",
                value=_KEEP_EXISTING_BINDING,
                description="Leave the current manifest.toml in place and exit.",
            ),
            SelectOption(
                label="Update binding",
                value=_UPDATE_EXISTING_BINDING,
                description="Replace the current manifest.toml with a new org/project binding.",
            ),
            SelectOption(
                label="Cancel",
                value=_CANCEL_EXISTING_BINDING,
                description="Abort without changing the current binding.",
            ),
        ],
        flow_chrome(PromptChrome(description=description)),
    )
    if choice == _KEEP_EXISTING_BINDING:
        console.print("Keeping the existing repository binding.")
        return False
    if choice == _CANCEL_EXISTING_BINDING:
        raise RuntimeError("Cancelled.")
    return True


def _resolve_binding_from_workspace_context(
    *,
    org_id: str | None,
    project_id: str | None,
    api_base_url: str | None = None,
) -> tuple[str | None, str | None]:
    if org_id and project_id:
        return org_id, project_id
    try:
        current_auth = read_auth_state()
    except RuntimeError:
        return org_id, project_id
    if current_auth is None:
        return org_id, project_id
    try:
        auth = ensure_valid_auth_state(current_auth)
    except RuntimeError:
        return org_id, project_id
    client = DopsClient(api_base_url=(api_base_url or auth.apiBaseUrl).rstrip("/"), token=auth.accessToken)
    context = load_session_context(auth.accessToken, api_base_url or auth.apiBaseUrl)
    if not context:
        return org_id, project_id
    resolved_org_id = org_id
    resolved_project_id = project_id
    if not resolved_org_id:
        org_options = _organization_options(context)
        org_choice = _pick_option(
            "Choose organization",
            org_options,
            description="Select the DecisionOps organization that should own this repository binding.",
            create_label="Create a new organization",
            create_value=_CREATE_ORGANIZATION,
            manual_label="Enter a different org_id",
        )
        if org_choice == _CREATE_ORGANIZATION:
            org_name = prompt_text(
                title="New organization name",
                placeholder="Acme Workspace",
                validate=lambda value: None if value else "Organization name is required.",
            )
            created_org = with_spinner(
                "Creating organization...",
                lambda: client.create_organization(org_name, auto_generate_service_token=False),
            )
            resolved_org_id = _organization_id(created_org)
            if not resolved_org_id:
                raise RuntimeError("DecisionOps API did not return an organization id for the new organization.")
            context = load_session_context(auth.accessToken, client.api_base_url) or context
        elif org_choice != _MANUAL_SELECTION:
            resolved_org_id = org_choice
            active_org_id = _organization_id(context.get("activeOrganization") if isinstance(context, dict) else None)
            if resolved_org_id != active_org_id:
                with_spinner("Switching active organization...", lambda: client.switch_active_org(resolved_org_id))
                context = load_session_context(auth.accessToken, client.api_base_url) or context
    if not resolved_project_id and resolved_org_id:
        project_options = _project_options(context, resolved_org_id)
        project_choice = _pick_option(
            "Choose project",
            project_options,
            description="Select the DecisionOps project that should govern this repository.",
            create_label="Create a new project",
            create_value=_CREATE_PROJECT,
            manual_label="Enter a different project_id",
        )
        if project_choice == _CREATE_PROJECT:
            project_name = prompt_text(
                title="New project name",
                placeholder="Payments Platform",
                validate=lambda value: None if value else "Project name is required.",
            )
            set_default = prompt_confirm(
                "Make this the default project for this organization?",
                False,
                flow_chrome(PromptChrome(description="Default projects are selected automatically for new sessions.")),
            )
            created_project = with_spinner(
                "Creating project...",
                lambda: client.create_project(project_name, set_default=set_default),
            )
            resolved_project_id = _project_id(created_project)
            if not resolved_project_id:
                raise RuntimeError("DecisionOps API did not return a project id for the new project.")
            if not set_default:
                with_spinner("Switching active project...", lambda: client.switch_active_project(resolved_project_id))
            context = load_session_context(auth.accessToken, client.api_base_url) or context
        elif project_choice != _MANUAL_SELECTION:
            resolved_project_id = project_choice
            active_project_id = _project_id(context.get("activeProject") if isinstance(context, dict) else None)
            if resolved_project_id != active_project_id:
                with_spinner("Switching active project...", lambda: client.switch_active_project(resolved_project_id))
                context = load_session_context(auth.accessToken, client.api_base_url) or context
    return resolved_org_id, resolved_project_id


def _resolve_non_interactive_binding(
    *,
    org_id: str | None,
    project_id: str | None,
    api_base_url: str | None = None,
    user_session_token: str | None = None,
) -> tuple[str | None, str | None]:
    if org_id and project_id:
        return org_id, project_id
    token = (user_session_token or "").strip()
    base_url = api_base_url
    if not token:
        try:
            current_auth = read_auth_state()
        except RuntimeError:
            return org_id, project_id
        if current_auth is None:
            return org_id, project_id
        try:
            auth = ensure_valid_auth_state(current_auth)
        except RuntimeError:
            return org_id, project_id
        token = auth.accessToken
        base_url = base_url or auth.apiBaseUrl
    context = load_session_context(token, base_url)
    if not context:
        return org_id, project_id
    resolved_org_id = org_id
    resolved_project_id = project_id
    if not resolved_org_id:
        org_options = _organization_options(context)
        if org_options:
            resolved_org_id = org_options[0].value
    if not resolved_project_id and resolved_org_id:
        project_options = _project_options(context, resolved_org_id)
        if project_options:
            resolved_project_id = project_options[0].value
    return resolved_org_id, resolved_project_id


def _parse_platform_args(positional: list[str] | None, flag_value: list[str] | str | None) -> list[str]:
    items: list[str] = []
    flag_items = [flag_value] if isinstance(flag_value, str) else list(flag_value or [])
    for raw in list(positional or []) + flag_items:
        for part in str(raw).replace(",", " ").split():
            clean = part.strip()
            if clean and clean not in items:
                items.append(clean)
    return items


def run_init(flags: argparse.Namespace) -> None:
    reset_flow_state()
    repo_path = resolve_repo_path(flags.repo_path)
    if not repo_path:
        raise RuntimeError("Could not determine repository path. Use --repo-path.")
    non_interactive = bool(getattr(flags, "non_interactive", False))
    existing_manifest, existing_manifest_error, manifest_exists = _load_existing_manifest(repo_path)
    if manifest_exists and existing_manifest_error:
        if non_interactive or not is_interactive():
            raise RuntimeError(
                f"{existing_manifest_error} Re-run `dops init` interactively to repair or replace the binding."
            )
        _confirm_rebinding_for_invalid_manifest(repo_path, existing_manifest_error)
    elif existing_manifest:
        if not non_interactive:
            if not is_interactive():
                raise RuntimeError(
                    "This repository already has .decisionops/manifest.toml. Re-run `dops init` interactively to confirm rebinding."
                )
            if not _confirm_rebinding(repo_path, existing_manifest, flags.api_base_url):
                return
    allow_placeholders = bool(flags.allow_placeholders)
    detected_repo_ref = normalize_repo_ref(flags.repo_ref) if flags.repo_ref else detect_repo_ref(repo_path)
    default_branch = flags.default_branch or infer_default_branch(repo_path)
    org_id = flags.org_id
    project_id = flags.project_id
    repo_ref = detected_repo_ref

    if not org_id and not project_id and allow_placeholders:
        org_id = PLACEHOLDER_ORG_ID
        project_id = PLACEHOLDER_PROJECT_ID
        repo_ref = repo_ref or PLACEHOLDER_REPO_REF
    elif not org_id or not project_id:
        if non_interactive or getattr(flags, "user_session_token", None):
            org_id, project_id = _resolve_non_interactive_binding(
                org_id=org_id,
                project_id=project_id,
                api_base_url=flags.api_base_url,
                user_session_token=getattr(flags, "user_session_token", None),
            )
        if not org_id or not project_id:
            if non_interactive or not is_interactive():
                raise RuntimeError("--org-id and --project-id are required. Use --allow-placeholders for local prototyping.")
            org_id, project_id = _resolve_binding_from_workspace_context(
                org_id=org_id,
                project_id=project_id,
                api_base_url=flags.api_base_url,
            )
            if not org_id:
                org_id = prompt_text(
                    title="DecisionOps org_id",
                    placeholder=PLACEHOLDER_ORG_ID if allow_placeholders else "org_...",
                    validate=lambda value: None if value else "org_id is required.",
                )
            if not project_id:
                project_id = prompt_text(
                    title="DecisionOps project_id",
                    placeholder=PLACEHOLDER_PROJECT_ID if allow_placeholders else "proj_...",
                    validate=lambda value: None if value else "project_id is required.",
                )
    if not repo_ref:
        if allow_placeholders:
            repo_ref = PLACEHOLDER_REPO_REF
        elif not non_interactive and is_interactive():
            repo_ref = normalize_repo_ref(
                prompt_text(
                    title="Repository reference (owner/repo)",
                    placeholder="owner/repo",
                    validate=lambda value: None if value else "repo_ref is required.",
                )
            )
        else:
            raise RuntimeError("Could not infer repo_ref. Pass --repo-ref or use --allow-placeholders.")
    manifest_path = write_manifest(
        repo_path,
        {
            "org_id": str(org_id),
            "project_id": str(project_id),
            "repo_ref": str(repo_ref),
            "default_branch": default_branch,
            "mcp_server_name": resolve_server_name(flags.server_name),
            "mcp_server_url": resolve_server_url(flags.server_url),
            "repo_id": flags.repo_id,
        },
    )
    console.print(f"Wrote manifest: {manifest_path}")
    link_status, link_message = _verify_or_attach_project_repository(
        org_id=str(org_id),
        project_id=str(project_id),
        repo_ref=str(repo_ref),
        api_base_url=flags.api_base_url,
        attach_missing=True,
    )
    if link_status == "ok":
        console.print(link_message)
    elif link_status == "warning":
        console.print(f"[yellow]{link_message}[/yellow]")
    else:
        raise RuntimeError(f"{link_message} The local manifest was written, but the central repository binding is still incomplete.")


def run_install(flags: argparse.Namespace) -> None:
    reset_flow_state()
    if flags.source_dir:
        platforms_dir, resolved_source_dir = resolve_local_skill_repo(flags.source_dir)
    else:
        platforms_dir = find_platforms_dir()
        resolved_source_dir = None
    requested_platforms = _parse_platform_args(getattr(flags, "platform", None), getattr(flags, "platforms", None))
    selected_platforms = choose_platforms(requested_platforms, platforms_dir, "Install")
    repo_path = resolve_repo_path(flags.repo_path)
    source_dir = resolved_source_dir or (None if flags.skip_skill else find_skill_source_dir())
    org_id = flags.org_id
    project_id = flags.project_id
    if not flags.skip_manifest and not flags.allow_placeholders and repo_path and (not org_id or not project_id):
        existing_manifest, _, _ = _load_existing_manifest(repo_path)
        if not existing_manifest:
            org_id, project_id = _resolve_non_interactive_binding(
                org_id=org_id,
                project_id=project_id,
                api_base_url=flags.api_base_url,
                user_session_token=getattr(flags, "user_session_token", None),
            )
    result = install_platforms(
        {
            "platforms_dir": platforms_dir,
            "selected_platforms": selected_platforms,
            "repo_path": repo_path,
            "org_id": org_id,
            "project_id": project_id,
            "repo_ref": flags.repo_ref,
            "repo_id": flags.repo_id,
            "default_branch": flags.default_branch,
            "install_skill": not flags.skip_skill,
            "install_mcp": not flags.skip_mcp,
            "write_manifest": not flags.skip_manifest,
            "allow_placeholders": flags.allow_placeholders,
            "output_dir": flags.output_dir,
            "source_dir": source_dir,
            "skill_name": flags.skill_name,
            "server_name": flags.server_name,
            "server_url": flags.server_url,
        }
    )
    render_install_summary(result)


def run_uninstall(flags: argparse.Namespace) -> None:
    reset_flow_state()
    platforms_dir = find_platforms_dir()
    selected_platforms = choose_platforms(flags.platform, platforms_dir, "Uninstall", with_descriptions=False)
    repo_path = resolve_repo_path(flags.repo_path)
    result = cleanup_platforms(
        {
            "platforms_dir": platforms_dir,
            "selected_platforms": selected_platforms,
            "repo_path": repo_path,
            "skill_name": flags.skill_name,
            "server_name": flags.server_name,
            "remove_skill": not flags.skip_skill,
            "remove_mcp": not flags.skip_mcp,
            "remove_manifest": flags.remove_manifest,
        }
    )
    render_cleanup_summary(result)
    if not flags.skip_auth:
        try:
            current = read_auth_state()
        except RuntimeError as error:
            console.print(f"[yellow]{error}[/yellow]")
            clear_auth_state()
            console.print("Removed the corrupt local auth state.")
            current = None
        if current:
            with_spinner("Revoking session...", lambda: revoke_auth_state(current))
            clear_auth_state()
            console.print("Removed local auth state.")


def _is_default_project_id(org_id: str, project_id: str) -> bool:
    normalized = project_id.strip()
    return normalized == "default" or normalized == f"{org_id.strip()}:default" or normalized.endswith(":default")


def run_doctor(flags: argparse.Namespace) -> None:
    repo_path = resolve_repo_path(flags.repo_path)
    issues: list[str] = []
    tls_info = describe_tls_setup()
    system_info = {
        "CLI version": __version__,
        "Python": platform.python_version(),
        "OS": f"{platform.system()} {platform.release()}",
        "Invocation path": str(Path(sys.argv[0]).resolve()),
        "Shell `dops` path": shutil.which("dops") or "(not on PATH)",
        "SSL backend": tls_info["ssl_backend"],
        "CA source": tls_info["ca_source"],
    }
    if config_error():
        issues.append(config_error() or "")
    try:
        current_auth = read_auth_state()
    except RuntimeError as error:
        current_auth = None
        issues.append(str(error))
    auth = None
    if current_auth:
        try:
            auth = ensure_valid_auth_state(current_auth)
        except RuntimeError as error:
            issues.append(str(error))
    if not auth:
        issues.append("CLI auth not configured")
    manifest = None
    if repo_path:
        try:
            manifest = read_manifest(repo_path)
        except InvalidManifestError as error:
            issues.append(str(error))
    if not repo_path:
        issues.append("Not inside a git repository")
    elif not manifest:
        issues.append("No .decisionops/manifest.toml found")
    elif not manifest.get("org_id") or not manifest.get("project_id") or not manifest.get("repo_ref"):
        issues.append("Manifest is missing required fields (org_id, project_id, or repo_ref)")
    elif auth:
        manifest_org_id = str(manifest.get("org_id") or "")
        manifest_project_id = str(manifest.get("project_id") or "")
        manifest_repo_ref = str(manifest.get("repo_ref") or "")
        link_status, link_message = _verify_or_attach_project_repository(
            org_id=manifest_org_id,
            project_id=manifest_project_id,
            repo_ref=manifest_repo_ref,
            api_base_url=auth.apiBaseUrl,
            attach_missing=False,
        )
        if link_status != "ok" and not _is_default_project_id(manifest_org_id, manifest_project_id):
            issues.append(link_message)
    platform_statuses: list[dict[str, str]] = []
    discovered_mcp_token: str | None = None
    server_name = (manifest.get("mcp_server_name") if manifest else None) or DEFAULT_MCP_SERVER_NAME
    expected_server_url = (manifest.get("mcp_server_url") if manifest else None) or DEFAULT_MCP_SERVER_URL
    try:
        platforms = load_platforms(find_platforms_dir())
        context = {"skill_name": "decision-ops", "repo_path": repo_path or ""}
        for platform_def in platforms.values():
            skill_path = resolve_install_path(platform_def.skill, context) if platform_def.skill and platform_def.skill.supported else None
            skill_installed = bool(skill_path and Path(skill_path).exists())
            mcp_path = resolve_install_path(platform_def.mcp, context) if platform_def.mcp and platform_def.mcp.supported else None
            mcp_status, mcp_detail, mcp_token = _resolve_mcp_status_with_token(
                platform_def=platform_def,
                config_path_str=mcp_path,
                server_name=str(server_name),
                expected_url=str(expected_server_url),
                issues=issues,
                skill_installed=skill_installed,
            )
            if mcp_token and not discovered_mcp_token:
                discovered_mcp_token = mcp_token
            platform_statuses.append(
                {
                    "displayName": platform_def.display_name,
                    "skillStatus": "n/a"
                    if not (platform_def.skill and platform_def.skill.supported)
                    else f"installed ({skill_path})"
                    if skill_installed
                    else "not installed",
                    "mcpStatus": mcp_status,
                    "mcpDetail": mcp_detail,
                }
            )
    except (RuntimeError, OSError, ValueError) as error:
        issues.append(_doctor_platform_issue(error))
        platform_statuses = []

    api_auth_probe: ApiAuthProbeResult | None = None
    mcp_reach_probe: McpReachabilityResult | None = None
    mcp_gate_probe: McpGateProbeResult | None = None
    if auth:
        # Two independent signals. The CLI token is scoped to the REST API
        # audience and literally cannot authenticate against /mcp (which
        # uses a separate OAuth audience for IDE MCP clients), so we probe
        # each endpoint with the right expectation instead of one-size-fits-all.
        api_auth_probe = probe_api_auth(api_base_url=auth.apiBaseUrl, token=auth.accessToken)
        if not api_auth_probe.reachable:
            issues.append(f"DecisionOps API auth probe failed: {api_auth_probe.short_status()}.")
    mcp_reach_probe = probe_mcp_reachability(mcp_url=str(expected_server_url))
    if not mcp_reach_probe.reachable:
        issues.append(f"DecisionOps MCP server not reachable: {mcp_reach_probe.short_status()}.")

    probe_token = discovered_mcp_token or (
        auth.accessToken if auth and str(auth.accessToken).startswith("dops_sk_") else None
    )
    if probe_token and mcp_reach_probe.reachable:
        inferred_repo_ref = (
            str(manifest.get("repo_ref") or "")
            if manifest
            else (detect_repo_ref(repo_path) if repo_path else None)
        ) or None
        mcp_gate_probe = probe_mcp_gate_resolution(
            mcp_url=str(expected_server_url),
            token=probe_token,
            org_id=str(manifest.get("org_id")) if manifest and manifest.get("org_id") else None,
            project_id=str(manifest.get("project_id")) if manifest and manifest.get("project_id") else None,
            repo_ref=inferred_repo_ref,
        )
        if not mcp_gate_probe.authenticated:
            issues.append(f"DecisionOps MCP bearer token check failed: {mcp_gate_probe.short_status()}.")
        elif not mcp_gate_probe.supports_project_provisioning:
            issues.append(
                "DecisionOps MCP server does not advertise `create_project_if_missing` / `project_name` on `do-prepare-decision-gate`."
            )
        elif (
            manifest
            and mcp_gate_probe.project_resolution == "mapped_repo"
            and mcp_gate_probe.resolved_project_id
            and str(manifest.get("project_id") or "") != mcp_gate_probe.resolved_project_id
        ):
            issues.append(
                f"Manifest `project_id` is `{manifest.get('project_id')}`, but `{manifest.get('repo_ref')}` is already mapped to project `{mcp_gate_probe.resolved_project_id}`. Update `.decisionops/manifest.toml` or run `dops init`."
            )

    render_doctor_report(
        auth=auth,
        auth_display=auth_display(auth) if auth else "",
        repo_path=repo_path,
        manifest=manifest,
        platforms=platform_statuses,
        issues=issues,
        system_info=system_info,
        cli_config_path=str(config_path()),
        cli_config_error=config_error(),
        api_auth_probe=api_auth_probe,
        mcp_reach_probe=mcp_reach_probe,
        mcp_gate_probe=mcp_gate_probe,
        mcp_expected_url=str(expected_server_url),
    )


def run_auth_mcp(flags: argparse.Namespace) -> None:
    """Execute MCP OAuth triggers for selected platforms.

    - `kind=cli` triggers shell out and stream output.
    - All other kinds render as instructions the user must follow.
    - `--reset` runs every platform's `reason=reset` triggers first so
      stale credentials get cleared before the primary login attempt.
    """
    if flags.source_dir:
        platforms_dir, _ = resolve_local_skill_repo(flags.source_dir)
    else:
        platforms_dir = find_platforms_dir()

    all_platforms = load_platforms(platforms_dir)
    repo_path = resolve_repo_path(None)
    manifest: dict[str, Any] | None = None
    if repo_path:
        try:
            manifest = read_manifest(repo_path)
        except InvalidManifestError:
            manifest = None

    server_name = (manifest.get("mcp_server_name") if manifest else None) or DEFAULT_MCP_SERVER_NAME
    server_url = (manifest.get("mcp_server_url") if manifest else None) or DEFAULT_MCP_SERVER_URL

    selected_ids: list[str]
    if flags.platform:
        missing = [pid for pid in flags.platform if pid not in all_platforms]
        if missing:
            raise RuntimeError(f"Unknown platform(s): {', '.join(missing)}")
        selected_ids = list(flags.platform)
    else:
        selected_ids = sorted(all_platforms.keys())

    context = {
        "display_name": "",  # filled per-platform below
        "mcp_server_name": str(server_name),
        "mcp_server_url": str(server_url),
        "skill_name": "decision-ops",
        "repo_path": repo_path or "",
    }

    any_failure = False
    for platform_id in selected_ids:
        platform = all_platforms[platform_id]
        per_platform_context = {**context, "display_name": platform.display_name}
        triggers = platform_triggers(platform, per_platform_context)
        if not triggers:
            console.print(f"[dim]{platform.display_name}: no auth triggers declared; skipping.[/dim]")
            continue
        console.print(f"\n[bold]{platform.display_name}[/bold]")

        ordered: list = []
        if flags.reset:
            ordered.extend(triggers_by_reason(triggers, "reset"))
        ordered.extend(triggers_by_reason(triggers, "primary"))

        for trigger in ordered:
            if trigger.kind == "cli":
                label = trigger.label or "Run"
                console.print(f"  → {label}: `{describe_trigger(trigger)}`")
                result = execute_cli_trigger(trigger)
                if result.status == "ran":
                    console.print("    [green]done[/green]")
                else:
                    any_failure = True
                    console.print(f"    [red]failed[/red] — {result.detail}")
            else:
                label = trigger.label or trigger.kind
                # Rich would parse `[slash]` as markup, so render the kind
                # with a dim color tag and escaped brackets inside that.
                console.print(f"  - [dim]({trigger.kind})[/dim] {label}")
                console.print(f"    {describe_trigger(trigger)}")

    if any_failure:
        raise RuntimeError("One or more auth triggers failed; see output above.")


def _resolve_mcp_status_with_token(
    *,
    platform_def,
    config_path_str: str | None,
    server_name: str,
    expected_url: str,
    issues: list[str],
    skill_installed: bool = True,
) -> tuple[str, str, str | None]:
    """Return (short_status, detail_path_or_blank, bearer_token) for this platform's MCP entry."""
    if not (platform_def.mcp and platform_def.mcp.supported):
        return "n/a", "", None
    if not config_path_str:
        return "not configured", "", None
    fmt = platform_def.mcp.format or ""
    report: McpEntryReport = inspect_mcp_entry(
        config_path=config_path_str,
        fmt=fmt,
        root_key=platform_def.mcp.root_key,
        server_name=server_name,
        expected_url=expected_url,
        platform_id=platform_def.id,
    )
    resolved_path = config_path_str
    if not report.healthy and getattr(platform_def, "id", None) == "claude-code":
        user_claude_path = str(Path(expand_home("~/.claude.json")).resolve())
        if user_claude_path != str(Path(config_path_str).resolve()) and Path(user_claude_path).exists():
            user_report = inspect_mcp_entry(
                config_path=user_claude_path,
                fmt="json_map",
                root_key="mcpServers",
                server_name=server_name,
                expected_url=expected_url,
                platform_id=platform_def.id,
            )
            if user_report.entry_found:
                report = user_report
                resolved_path = user_claude_path
    if report.config_exists or skill_installed:
        for issue in report.issues:
            # Prefix with the platform so users know which IDE to go fix.
            issues.append(f"[{platform_def.display_name}] {issue}")
    if not report.config_exists:
        return "not configured", resolved_path, None
    return report.short_status(), resolved_path, report.bearer_token


def _resolve_mcp_status(
    *,
    platform_def,
    config_path_str: str | None,
    server_name: str,
    expected_url: str,
    issues: list[str],
) -> tuple[str, str]:
    status, detail, _ = _resolve_mcp_status_with_token(
        platform_def=platform_def,
        config_path_str=config_path_str,
        server_name=server_name,
        expected_url=expected_url,
        issues=issues,
        skill_installed=True,
    )
    return status, detail


def register_repo_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init = subparsers.add_parser(
        "init",
        formatter_class=DopsHelpFormatter,
        help="Bind the current repository to a DecisionOps project",
        description="Bind the current repository to a DecisionOps project",
    )
    init.add_argument("--repo-path", help="Path to the repository to bind (defaults to current git repository)")
    init.add_argument("--api-base-url", help="DecisionOps API base URL override")
    init.add_argument("--org-id", help="DecisionOps organization ID")
    init.add_argument("--project-id", help="DecisionOps project ID")
    init.add_argument("--repo-ref", help="Repository reference in owner/repo format")
    init.add_argument("--repo-id", help="Explicit DecisionOps repository ID")
    init.add_argument("--default-branch", help="Default git branch name (e.g. main)")
    init.add_argument("--user-session-token", help="Bearer access token for non-interactive project discovery")
    init.add_argument("--allow-placeholders", action="store_true", help="Write placeholder values when org/project binding is not yet known")
    init.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts, discovering active project via API when needed")
    init.add_argument("--server-name", help="MCP server name override (default: decision-ops-mcp)")
    init.add_argument("--server-url", help="MCP server URL override")
    init.set_defaults(func=run_init)
    add_examples(init, ["dops init --org-id acme --project-id backend --repo-ref acme/backend", "dops init --allow-placeholders"])

    install = subparsers.add_parser(
        "install",
        formatter_class=DopsHelpFormatter,
        help="Install DecisionOps skill + MCP config for chosen platforms",
        description="Install DecisionOps skill + MCP config for chosen platforms",
    )
    install.add_argument("platform", nargs="*", help="Platform ID(s) to install (e.g. codex, cursor, claude-code)")
    install.add_argument("--platforms", action="append", help="Comma-separated or repeatable platform ID(s) to install")
    install.add_argument("--repo-path", help="Path to the target repository")
    install.add_argument("--api-base-url", help="DecisionOps API base URL override")
    install.add_argument("--org-id", help="DecisionOps organization ID")
    install.add_argument("--project-id", help="DecisionOps project ID")
    install.add_argument("--repo-ref", help="Repository reference in owner/repo format")
    install.add_argument("--repo-id", help="Explicit DecisionOps repository ID")
    install.add_argument("--default-branch", help="Default git branch name (e.g. main)")
    install.add_argument("--user-session-token", help="Bearer access token for non-interactive project discovery")
    install.add_argument("--allow-placeholders", action="store_true", help="Allow placeholder manifest values without interactive prompts")
    install.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts")
    install.add_argument("--skip-manifest", action="store_true", help="Skip writing .decisionops/manifest.toml in the repository")
    install.add_argument("--skip-skill", action="store_true", help="Skip installing the DecisionOps skill bundle")
    install.add_argument("--skip-mcp", action="store_true", help="Skip configuring the DecisionOps MCP server entry")
    install.add_argument("--output-dir", help="Intermediate build output directory")
    install.add_argument("--source-dir", help="Local skill repository or decision-ops bundle directory")
    install.add_argument("--skill-name", help="Skill bundle name override (default: decision-ops)")
    install.add_argument("--server-name", help="MCP server name override (default: decision-ops-mcp)")
    install.add_argument("--server-url", help="MCP server URL override")
    install.add_argument("-y", "--yes", action="store_true", help="Accept default selections without interactive confirmation")
    install.set_defaults(func=run_install)
    add_examples(
        install,
        [
            "dops install",
            "dops install codex",
            "dops install codex cursor",
            "dops install codex --skip-mcp",
        ],
    )
    add_notes(
        install,
        [
            "Platform means the editor or coding agent target to install into, such as Codex, Cursor, Claude Code, or VS Code.",
            "Available platform ids come from the downloaded DecisionOps skill repo. Run `dops platform list` to inspect them.",
        ],
    )

    uninstall = subparsers.add_parser(
        "uninstall",
        formatter_class=DopsHelpFormatter,
        help="Remove installed DecisionOps skills, MCP entries, and local auth state",
        description="Remove installed DecisionOps skills, MCP entries, and local auth state",
        aliases=["cleanup"],
    )
    uninstall.add_argument("platform", nargs="*", help="Platform ID(s) to uninstall (defaults to all platforms)")
    uninstall.add_argument("--repo-path", help="Path to the target repository")
    uninstall.add_argument("--skill-name", help="Skill bundle name override (default: decision-ops)")
    uninstall.add_argument("--server-name", help="MCP server name override (default: decision-ops-mcp)")
    uninstall.add_argument("--skip-skill", action="store_true", help="Keep installed skill bundles")
    uninstall.add_argument("--skip-mcp", action="store_true", help="Keep configured MCP server entries")
    uninstall.add_argument("--skip-auth", action="store_true", help="Keep local DecisionOps authentication state")
    uninstall.add_argument("--remove-manifest", action="store_true", help="Also remove .decisionops/manifest.toml from the repository")
    uninstall.set_defaults(func=run_uninstall)
    add_examples(
        uninstall,
        [
            "dops uninstall",
            "dops uninstall codex",
            "dops uninstall claude-code --remove-manifest --skip-auth",
        ],
    )
    add_notes(
        uninstall,
        [
            "Platform means the editor or coding agent target whose installed DecisionOps files should be removed.",
            "Available platform ids come from the downloaded DecisionOps skill repo. Run `dops platform list` to inspect them.",
        ],
    )

    doctor = subparsers.add_parser(
        "doctor",
        formatter_class=DopsHelpFormatter,
        help="Diagnose local DecisionOps setup and suggest fixes",
        description="Diagnose local DecisionOps setup and suggest fixes",
    )
    doctor.add_argument("--repo-path", help="Path to the repository to inspect")
    doctor.set_defaults(func=run_doctor)
    add_examples(doctor, ["dops doctor", "dops doctor --repo-path ~/projects/my-repo"])
