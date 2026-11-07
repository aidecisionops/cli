from __future__ import annotations

import sys
from dataclasses import replace
from typing import Any

from rich.panel import Panel

from ..api_client import DecisionOpsApiError, DopsClient, load_user_context
from ..auth import AuthState, write_auth_state
from ..config import DEFAULT_MCP_SERVER_NAME, DEFAULT_MCP_SERVER_URL
from ..git import infer_repo_ref
from ..platforms import load_platforms
from ..runtime import emit_diagnostic
from ..ui import (
    PromptChrome,
    SelectOption,
    console,
    flow_chrome,
    prompt_confirm,
    prompt_select,
    with_spinner,
)


def parse_scopes(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [item.strip() for item in raw.replace(",", " ").split() if item.strip()]


def resolve_identity(context: dict[str, Any] | None, fallback: str | None = None) -> str | None:
    user = (context or {}).get("user") or {}
    return user.get("email") or user.get("displayName") or user.get("id") or fallback


def resolve_organization(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    organization = context.get("activeOrganization") or ((context.get("organizations") or [None])[0])
    if not organization:
        return None
    org_name = str(organization.get("orgName", ""))
    org_id = str(organization.get("orgId", ""))
    return org_name if org_name == org_id else f"{org_name} ({org_id})"


def resolve_auth_user(context: dict[str, Any] | None) -> dict[str, str] | None:
    user = (context or {}).get("user") or {}
    auth_user = {}
    if user.get("id"):
        auth_user["id"] = str(user["id"]).strip()
    if user.get("email"):
        auth_user["email"] = str(user["email"]).strip()
    if user.get("displayName"):
        auth_user["name"] = str(user["displayName"]).strip()
    return auth_user or None


def persist_auth_user(auth: AuthState, context: dict[str, Any] | None) -> AuthState:
    user = resolve_auth_user(context)
    if not user or auth.user == user:
        return auth
    next_state = replace(auth, user=user)
    write_auth_state(next_state)
    return next_state


def load_session_context(token: str, api_base_url: str | None = None) -> dict[str, Any] | None:
    try:
        return with_spinner("Loading DecisionOps workspace...", lambda: load_user_context(token=token, apiBaseUrl=api_base_url))
    except (DecisionOpsApiError, RuntimeError) as error:
        emit_diagnostic(f"Could not load DecisionOps workspace context: {error}")
        return None


def print_login_summary(lines: list[str]) -> None:
    console.print(Panel.fit("\n".join(lines), border_style="cyan"))


def normalize_repo_ref(value: str) -> str:
    normalized = value.strip().rstrip("/").removesuffix(".git")
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/", "ssh://git@github.com/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


def detect_repo_ref(repo_path: str) -> str | None:
    try:
        return normalize_repo_ref(infer_repo_ref(repo_path))
    except RuntimeError:
        return None


def is_interactive() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def choose_platforms(
    initial_ids: list[str] | None,
    platforms_dir: str,
    eyebrow: str,
    *,
    with_descriptions: bool = True,
) -> list[str]:
    if initial_ids:
        return initial_ids
    if not is_interactive():
        example_command = "dops install codex" if eyebrow == "Install" else "dops uninstall codex"
        raise RuntimeError(f"No install target selected. Re-run interactively or pass platform ids like `{example_command}`.")
    platforms = list(load_platforms(platforms_dir).values())
    chosen: set[str] = set()
    add_another = True
    is_install = eyebrow == "Install"
    title = (
        "Which editor or coding agent should DecisionOps install into?"
        if is_install
        else "Which editor or coding agent should DecisionOps clean up?"
    )
    description = (
        "Platform means the target editor or agent integration, such as Codex, Cursor, Claude Code, or VS Code. "
        "You can pick more than one."
        if is_install
        else "Platform means the target editor or agent integration whose DecisionOps files should be removed."
    )
    while add_another:
        platform_id = prompt_select(
            title,
            [
                SelectOption(
                    label=platform.display_name,
                    value=platform.id,
                    description=(f"Platform id: {platform.id}" if with_descriptions else None),
                )
                for platform in platforms
            ],
            flow_chrome(PromptChrome(eyebrow=eyebrow, description=description)),
        )
        chosen.add(platform_id)
        remaining = [platform for platform in platforms if platform.id not in chosen]
        if not remaining:
            break
        add_another = prompt_confirm("Add another platform?", False, flow_chrome(PromptChrome(eyebrow=eyebrow)))
    return list(chosen)


def auth_display(auth: AuthState) -> str:
    from ..auth import is_expired

    identity = (auth.user or {}).get("email") or (auth.user or {}).get("name") or (auth.user or {}).get("id") or "unknown"
    expiry = f"{auth.expiresAt}{' (expired)' if is_expired(auth) else ''}" if auth.expiresAt else "session"
    return f"{identity} via {auth.method} • {expiry}"


def decision_id(decision: dict[str, Any]) -> str:
    return str(decision.get("decisionId") or decision.get("id") or "")


def project_repository_refs(payload: dict[str, Any] | None) -> list[str]:
    repositories = (payload or {}).get("repositories") if isinstance(payload, dict) else None
    if not isinstance(repositories, list):
        return []
    refs: list[str] = []
    for repository in repositories:
        if isinstance(repository, str):
            repo_ref = repository.strip()
        elif isinstance(repository, dict):
            repo_ref = str(
                repository.get("repoRef")
                or repository.get("repo_ref")
                or repository.get("repoId")
                or repository.get("id")
                or ""
            ).strip()
        else:
            repo_ref = ""
        if repo_ref:
            refs.append(repo_ref)
    return refs


def project_repository_link_issue(project_id: str, repo_ref: str, repositories: list[str]) -> str | None:
    if repo_ref in repositories:
        return None
    if not repositories:
        return (
            f"Project `{project_id}` has no linked repositories in DecisionOps. Project-scoped decisions can still be "
            f"recorded, but repo-scoped drafts and repo_ref-based resolution require a linked repository. Link "
            f"`{repo_ref}` to this project to enable repository-scoped decisions for this repo."
        )
    linked_repositories = ", ".join(sorted(repositories))
    return (
        f"Project `{project_id}` is linked to {linked_repositories}, but not `{repo_ref}`. Project-scoped decisions "
        "can still be recorded, but repo-scoped drafts for this repository require the project repository mapping to "
        "include the current repo."
    )


def require_project_binding(client: DopsClient) -> tuple[str, str]:
    if not client.org_id or not client.project_id:
        raise RuntimeError("This command requires a bound repository. Run `dops init` first.")
    return client.org_id, client.project_id


def resolve_server_name(value: str | None) -> str:
    return value or DEFAULT_MCP_SERVER_NAME


def resolve_server_url(value: str | None) -> str:
    return value or DEFAULT_MCP_SERVER_URL
