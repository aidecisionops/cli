from __future__ import annotations

import argparse
import sys

from ..argparse_utils import DopsHelpFormatter, add_examples
from ..auth import (
    clear_auth_state,
    default_client_id,
    ensure_valid_auth_state,
    login_with_pkce,
    read_auth_state,
    revoke_auth_state,
    save_token_auth_state,
)
from ..ui import console, reset_flow_state, run_login_flow, with_spinner
from .shared import load_session_context, parse_scopes, persist_auth_user, print_login_summary, resolve_identity, resolve_organization


def run_login(flags: argparse.Namespace) -> None:
    reset_flow_state()
    scopes = parse_scopes(flags.scopes)
    auth_options = {
        "apiBaseUrl": flags.api_base_url,
        "issuerUrl": flags.issuer_url,
        "clientId": flags.client_id,
        "audience": flags.audience,
        "scopes": scopes,
    }
    if flags.clear:
        try:
            current = read_auth_state()
        except RuntimeError:
            current = None
        if current:
            with_spinner("Revoking session...", lambda: revoke_auth_state(current))
        clear_auth_state()
        console.print("Cleared saved auth state.")
        return
    if not flags.with_token and not flags.force:
        try:
            current = read_auth_state()
        except RuntimeError as error:
            console.print(f"[yellow]{error}[/yellow]")
            clear_auth_state()
            current = None
        if current:
            try:
                auth = with_spinner("Checking existing DecisionOps session...", lambda: ensure_valid_auth_state(current))
            except RuntimeError as error:
                console.print(f"[yellow]{error}[/yellow]")
                if current.method.startswith("token") or current.method.startswith("env:"):
                    return
                clear_auth_state()
                auth = None
            if auth is None:
                current = None
            else:
                context = load_session_context(auth.accessToken, auth.apiBaseUrl)
                auth = persist_auth_user(auth, context)
                if context:
                    print_login_summary(
                        [
                            "You are already logged into AI DecisionOps",
                            *([f"Logged into org: {resolve_organization(context)}"] if resolve_organization(context) else ["Saved session is ready to use"]),
                            *([f"Authenticated as: {resolve_identity(context)}"] if resolve_identity(context) else []),
                            "Run `dops logout` if you want to sign in again.",
                        ]
                    )
                    return
    if flags.with_token or flags.token:
        token = (flags.token or "").strip()
        if not token:
            raise RuntimeError("Pass --token with an already-issued DecisionOps bearer access token. Raw org API keys are not accepted here.")
        state, storage_path = save_token_auth_state(token=token, **auth_options)
        context = load_session_context(token, state.apiBaseUrl)
        persist_auth_user(state, context)
        console.print(f"Saved auth token -> {storage_path}")
        print_login_summary(
            [
                "Welcome to AI DecisionOps",
                *([f"Logged into org: {resolve_organization(context)}"] if resolve_organization(context) else ["Advanced token saved for dops CLI"]),
                *([f"Authenticated as: {resolve_identity(context)}"] if resolve_identity(context) else []),
            ]
        )
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        flow_result = run_login_flow(
            client_display=flags.client_id or default_client_id(),
            login_with_browser=lambda on_authorize_url: login_with_pkce(
                openBrowser=not flags.no_browser,
                onAuthorizeUrl=on_authorize_url,
                **auth_options,
            ),
        )
    else:
        printed = False

        def on_authorize_url(url: str) -> None:
            nonlocal printed
            if printed:
                return
            printed = True
            console.print("Open this URL in your browser to continue authentication:")
            console.print(url)

        flow_result = login_with_pkce(openBrowser=not flags.no_browser, onAuthorizeUrl=on_authorize_url, **auth_options)
    context = load_session_context(flow_result.state.accessToken, flow_result.state.apiBaseUrl)
    saved_state = persist_auth_user(flow_result.state, context)
    identity = (
        (saved_state.user or {}).get("email")
        or (saved_state.user or {}).get("name")
        or (saved_state.user or {}).get("id")
        or "unknown"
    )
    console.print(f"Saved -> {flow_result.storagePath}")
    print_login_summary(
        [
            "Welcome to AI DecisionOps",
            *([f"Logged into org: {resolve_organization(context)}"] if resolve_organization(context) else ["Signed into dops CLI successfully"]),
            *([f"Authenticated as: {resolve_identity(context, identity)}"] if resolve_identity(context, identity) else []),
        ]
    )


def run_logout() -> None:
    try:
        current = read_auth_state()
    except RuntimeError as error:
        console.print(f"[yellow]{error}[/yellow]")
        clear_auth_state()
        console.print("Removed the corrupt local session file.")
        return
    if not current:
        console.print("No DecisionOps session stored locally.")
        return
    with_spinner("Revoking session...", lambda: revoke_auth_state(current))
    clear_auth_state()
    console.print("Logged out and removed the local session.")


def run_auth_status() -> int:
    from ..auth import ensure_valid_auth_state, read_auth_state
    from ..ui import render_auth_status

    try:
        current = read_auth_state()
    except RuntimeError as error:
        console.print(str(error))
        return 1
    if not current:
        console.print("Auth: missing")
        return 1
    try:
        auth = ensure_valid_auth_state(current)
    except RuntimeError as error:
        console.print(f"Auth: expired or invalid — {error}")
        console.print("[dim]Run `dops login --force` to re-authenticate.[/dim]")
        return 1
    render_auth_status(auth)
    return 0


def register_auth_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    login = subparsers.add_parser(
        "login",
        formatter_class=DopsHelpFormatter,
        help="Authenticate this machine with DecisionOps",
        description="Authenticate this machine with DecisionOps",
    )
    login.add_argument("--api-base-url", help="DecisionOps API base URL override")
    login.add_argument("--issuer-url", help="OAuth / OIDC issuer URL override")
    login.add_argument("--client-id", help="OAuth client ID override")
    login.add_argument("--audience", help="OAuth audience override")
    login.add_argument("--scopes", help="Comma- or space-separated OAuth scopes to request")
    login.add_argument("--web", action="store_true", help="Use browser-based PKCE login (default)")
    login.add_argument("--with-token", action="store_true", help=argparse.SUPPRESS)
    login.add_argument("--token", help="Persist an already-issued DecisionOps bearer access token instead of starting browser login")
    login.add_argument("--no-browser", action="store_true", help="Do not attempt to launch a browser automatically")
    login.add_argument("--force", action="store_true", help="Start a new browser login even if a saved session already exists")
    login.add_argument("--clear", action="store_true", help="Remove saved login state")
    login.set_defaults(func=run_login)
    add_examples(login, ["dops login", "dops login --web", "dops login --token dop_..."])

    logout = subparsers.add_parser(
        "logout",
        formatter_class=DopsHelpFormatter,
        help="Revoke and remove the local DecisionOps session",
        description="Revoke and remove the local DecisionOps session",
    )
    logout.set_defaults(func=lambda args: run_logout())

    auth = subparsers.add_parser(
        "auth",
        formatter_class=DopsHelpFormatter,
        help="Inspect or manage the current DecisionOps auth session",
        description="Inspect or manage the current DecisionOps auth session",
    )
    auth.set_defaults(func=lambda args: auth.print_help() or 0)
    auth_subparsers = auth.add_subparsers(dest="auth_command")
    add_examples(auth, ["dops auth status", "dops auth mcp codex"])
    auth_status = auth_subparsers.add_parser(
        "status",
        formatter_class=DopsHelpFormatter,
        help="Show the current auth session",
        description="Show the current auth session",
    )
    auth_status.set_defaults(func=lambda args: run_auth_status())

    # `dops auth mcp <platform>` triggers each IDE's declared MCP auth
    # actions. Kept here (not in repo.py) to keep the auth command group
    # cohesive and avoid subcommand collisions.
    from .repo import run_auth_mcp  # local import to sidestep circular deps

    auth_mcp = auth_subparsers.add_parser(
        "mcp",
        formatter_class=DopsHelpFormatter,
        help="Trigger MCP OAuth for one or more installed IDEs",
        description=(
            "Runs each platform's declared auth triggers. Codex (and any future IDE "
            "with a CLI surface) is executed directly; IDEs that only expose slash "
            "commands or UI paths print their instructions for you to follow."
        ),
    )
    auth_mcp.add_argument(
        "platform",
        nargs="*",
        help="Platform id(s) to authenticate (e.g. codex, claude-code). Defaults to all installed.",
    )
    auth_mcp.add_argument("--reset", action="store_true", help="Run `reason=reset` triggers before `primary` to clear stale credentials.")
    auth_mcp.add_argument("--source-dir", help="Load platform definitions from a local skill checkout instead of the cached download.")
    auth_mcp.set_defaults(func=run_auth_mcp)
    add_examples(auth_mcp, ["dops auth mcp codex", "dops auth mcp codex claude-code", "dops auth mcp --reset codex"])
