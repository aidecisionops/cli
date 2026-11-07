from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterable, TypeVar

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.validation import ValidationError, Validator
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

T = TypeVar("T")


def _force_terminal() -> bool | None:
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return None


console = Console(stderr=False, force_terminal=_force_terminal(), no_color=bool(os.environ.get("NO_COLOR")))
error_console = Console(stderr=True, force_terminal=_force_terminal(), no_color=bool(os.environ.get("NO_COLOR")))

_prompt_count = 0


class CancelledError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Cancelled.")


@dataclass(slots=True)
class SelectOption(Generic[T]):
    label: str
    value: T
    description: str | None = None


@dataclass(slots=True)
class PromptChrome:
    eyebrow: str | None = None
    description: str | None = None
    footer: str | None = None
    show_brand_header: bool = True


def _supports_unicode_output() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def _status_symbol(kind: str) -> str:
    unicode_symbols = {
        "ok": "✓",
        "skip": "⊘",
        "remove": "✗",
        "next": "→",
    }
    ascii_symbols = {
        "ok": "OK",
        "skip": "-",
        "remove": "x",
        "next": "->",
    }
    symbols = unicode_symbols if _supports_unicode_output() else ascii_symbols
    return symbols.get(kind, kind)


def _status_markup(kind: str, style: str) -> str:
    return f"[{style}]{_status_symbol(kind)}[/{style}]"


def reset_flow_state() -> None:
    global _prompt_count
    _prompt_count = 0


def flow_chrome(chrome: PromptChrome | None = None) -> PromptChrome:
    global _prompt_count
    is_first = _prompt_count == 0
    _prompt_count += 1
    chrome = chrome or PromptChrome()
    return PromptChrome(
        eyebrow=chrome.eyebrow,
        description=chrome.description,
        footer=chrome.footer,
        show_brand_header=is_first,
    )


def _render_prompt_header(title: str, chrome: PromptChrome | None = None) -> None:
    chrome = chrome or PromptChrome()
    if chrome.show_brand_header:
        heading = Text("DecisionOps", style="bold cyan")
        heading.append(" CLI", style="dim")
        console.print(heading)
        if chrome.eyebrow:
            console.print(f"[dim]{chrome.eyebrow}[/dim]")
    console.print(f"[bold]{title}[/bold]")
    if chrome.description:
        console.print(f"[dim]{chrome.description}[/dim]")
    if chrome.footer:
        console.print(f"[dim]{chrome.footer}[/dim]")


def _resolve_select_value(raw_value: str, options: list[SelectOption[T]]) -> T | None:
    normalized = raw_value.strip()
    if not normalized:
        return None
    for index, option in enumerate(options, start=1):
        if normalized in {str(index), str(option.value), option.label}:
            return option.value
    return None


def _resolve_confirm_value(raw_value: str, default_value: bool) -> bool | None:
    normalized = raw_value.strip().lower()
    if not normalized:
        return default_value
    if normalized in {"y", "yes"}:
        return True
    if normalized in {"n", "no"}:
        return False
    return None


def _ensure_interactive() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Interactive terminal required.")


def prompt_select(title: str, options: list[SelectOption[T]], chrome: PromptChrome | None = None) -> T:
    _ensure_interactive()
    _render_prompt_header(title, chrome)
    for index, option in enumerate(options, start=1):
        console.print(f"  [cyan]{index}.[/cyan] {option.label}")
        if option.description:
            console.print(f"     [dim]{option.description}[/dim]")
    completer = WordCompleter(
        [str(index) for index in range(1, len(options) + 1)] + [str(option.value) for option in options],
        ignore_case=True,
    )
    try:
        raw_value = prompt(
            "> ",
            completer=completer,
            complete_while_typing=True,
            placeholder="Enter a number or platform id",
            validator=Validator.from_callable(
                lambda value: _resolve_select_value(value, options) is not None,
                error_message="Enter one of the listed numbers or platform ids.",
                move_cursor_to_end=True,
            ),
            validate_while_typing=False,
        )
    except (EOFError, KeyboardInterrupt) as error:
        raise CancelledError() from error
    result = _resolve_select_value(raw_value, options)
    if result is None:
        raise CancelledError()
    return result


def prompt_confirm(title: str, default_value: bool = True, chrome: PromptChrome | None = None) -> bool:
    _ensure_interactive()
    _render_prompt_header(title, chrome)
    default_hint = "Y/n" if default_value else "y/N"
    try:
        raw_value = prompt(
            f"> [{default_hint}] ",
            completer=WordCompleter(["yes", "no", "y", "n"], ignore_case=True),
            complete_while_typing=True,
            validator=Validator.from_callable(
                lambda value: _resolve_confirm_value(value, default_value) is not None,
                error_message="Enter yes or no.",
                move_cursor_to_end=True,
            ),
            validate_while_typing=False,
        )
    except (EOFError, KeyboardInterrupt) as error:
        raise CancelledError() from error
    result = _resolve_confirm_value(raw_value, default_value)
    if result is None:
        raise CancelledError()
    return result


def prompt_text(
    *,
    title: str,
    chrome: PromptChrome | None = None,
    default_value: str | None = None,
    placeholder: str | None = None,
    secret: bool = False,
    validate: Callable[[str], str | None] | None = None,
) -> str:
    _ensure_interactive()
    _render_prompt_header(title, chrome)

    class _PromptValidator(Validator):
        def validate(self, document) -> None:
            if validate is None:
                return
            value = document.text.strip() or (default_value or "").strip()
            error = validate(value)
            if error:
                raise ValidationError(message=error, cursor_position=len(document.text))

    try:
        value = prompt(
            "> ",
            default=default_value or "",
            is_password=secret,
            validator=_PromptValidator(),
            validate_while_typing=False,
            placeholder=placeholder or "",
        )
    except (EOFError, KeyboardInterrupt) as error:
        raise CancelledError() from error
    normalized = value.strip()
    return normalized if normalized else (default_value or "").strip()


def with_spinner(label: str, fn: Callable[[], T]) -> T:
    if sys.stdout.isatty():
        with console.status(f"[cyan]{label}[/cyan]"):
            return fn()
    return fn()


def run_login_flow(*, client_display: str, login_with_browser: Callable[[Callable[[str], None]], Any]) -> Any:
    _ensure_interactive()
    printed_url = False

    def on_authorize_url(url: str) -> None:
        nonlocal printed_url
        if printed_url:
            return
        printed_url = True
        console.print(
            Panel.fit(
                Text.from_markup(
                    f"[bold]Browser authentication[/bold]\n\nOpen this URL in your browser to continue:\n[cyan]{url}[/cyan]\n\n[dim]OAuth client: {client_display}[/dim]"
                ),
                border_style="cyan",
                title="Auth",
            )
        )

    return with_spinner("Waiting for browser authentication...", lambda: login_with_browser(on_authorize_url))


def _section_title(title: str) -> None:
    console.print(f"\n[bold cyan]{title}[/bold cyan]")


def render_install_summary(result) -> None:
    _section_title("Install summary")
    if result.manifest_path:
        suffix = " (placeholder)" if result.placeholders_used else ""
        console.print(f"{_status_markup('ok', 'green')} Manifest{suffix}: {result.manifest_path}")
    for entry in result.installed_skills:
        console.print(f"{_status_markup('ok', 'green')} Skill installed: {entry['target']} ({entry['platformId']})")
    for entry in result.installed_mcp:
        console.print(f"{_status_markup('ok', 'green')} MCP config written: {entry['target']} ({entry['platformId']})")
    for entry in result.skipped_mcp:
        console.print(f"{_status_markup('skip', 'yellow')} MCP config skipped: {entry['platformId']} - {entry['reason']}")
    if result.installed_skills or result.installed_mcp:
        _section_title("Next steps")
        console.print("1. Restart your IDE so it re-reads the updated MCP config.")
        # Platform-specific OAuth trigger, pulled from each platform's
        # auth.instructions in the skill catalog. Codex has a clean CLI
        # (`codex mcp login`), others are palette/UI paths — but they all
        # beat the old "invoke any tool and hope it prompts" text.
        for index, entry in enumerate(result.installed_mcp, start=2):
            platform_label = entry.get("displayName") or entry.get("platformId") or "platform"
            console.print(f"{index}. Authenticate MCP for {platform_label}:")
            steps = entry.get("authSteps") or []
            if not steps:
                console.print(
                    "   - Invoke any DecisionOps MCP tool in your IDE to trigger the browser OAuth flow."
                )
                continue
            for step in steps:
                console.print(f"   - {step}")
        if not result.installed_mcp:
            console.print("2. Invoke any DecisionOps MCP tool once to trigger the auth handoff.")


def render_cleanup_summary(result) -> None:
    _section_title("Cleanup summary")
    for entry in result.removed_skills:
        console.print(f"{_status_markup('remove', 'red')} Skill removed: {entry['target']} ({entry['platformId']})")
    for entry in result.skipped_skills:
        console.print(f"{_status_markup('skip', 'dim')} Skill skipped: {entry['platformId']} - {entry['reason']}")
    for entry in result.removed_mcp:
        console.print(f"{_status_markup('remove', 'red')} MCP config removed: {entry['target']} ({entry['platformId']})")
    for entry in result.skipped_mcp:
        console.print(f"{_status_markup('skip', 'dim')} MCP config skipped: {entry['platformId']} - {entry['reason']}")
    if result.removed_manifest_path:
        console.print(f"{_status_markup('remove', 'red')} Manifest removed: {result.removed_manifest_path}")
    if result.removed_mcp:
        console.print("[yellow]Restart your IDE to stop using the removed MCP server.[/yellow]")


def _colorize_mcp_status(status: str) -> str:
    lowered = status.lower()
    if lowered == "ok":
        return "[green]ok[/green]"
    if lowered == "n/a":
        return "[dim]n/a[/dim]"
    if lowered.startswith("wrong url") or lowered in {"disabled", "parse error"}:
        return f"[red]{status}[/red]"
    return f"[yellow]{status}[/yellow]"


def render_doctor_report(
    *,
    auth,
    auth_display: str,
    repo_path: str | None,
    manifest,
    platforms: Iterable[dict[str, str]],
    issues: list[str],
    system_info: dict[str, str] | None = None,
    cli_config_path: str | None = None,
    cli_config_error: str | None = None,
    api_auth_probe=None,
    mcp_reach_probe=None,
    mcp_gate_probe=None,
    mcp_expected_url: str | None = None,
) -> None:
    _section_title("DecisionOps Doctor")
    if system_info:
        table = Table(title="System", box=box.SIMPLE)
        table.add_column("Item")
        table.add_column("Value")
        for key, value in system_info.items():
            table.add_row(key, value)
        console.print(table)
    if cli_config_path:
        console.print(f"CLI config (`config.toml`): {cli_config_path}")
        console.print("[dim]  Distinct from the repo binding manifest at `.decisionops/manifest.toml`.[/dim]")
    if cli_config_error:
        console.print(f"[yellow]CLI config warning:[/yellow] {cli_config_error}")
    if auth:
        console.print(f"{_status_markup('ok', 'green')} CLI auth: configured ({auth_display})")
    else:
        console.print(f"{_status_markup('remove', 'red')} CLI auth: not configured")
        console.print(f"[dim]  {_status_symbol('next')} Run: dops login[/dim]")
    if repo_path:
        console.print(f"Repository: {repo_path}")
        if manifest:
            console.print("[green]Repo binding manifest (`manifest.toml`): present[/green]")
            console.print(f"[dim]  org_id:     {manifest.get('org_id', '(missing)')}[/dim]")
            console.print(f"[dim]  project_id: {manifest.get('project_id', '(missing)')}[/dim]")
            console.print(f"[dim]  repo_ref:   {manifest.get('repo_ref', '(missing)')}[/dim]")
        else:
            console.print("[red]Repo binding manifest (`manifest.toml`): missing[/red]")
            console.print(f"[dim]  {_status_symbol('next')} Run: dops init[/dim]")
    else:
        console.print("[dim]Repository: not detected (run from a git repo or pass --repo-path)[/dim]")
    table = Table(title="Platforms", box=box.SIMPLE)
    table.add_column("Platform")
    table.add_column("Skill")
    table.add_column("MCP entry")
    table.add_column("Config path")
    for platform in platforms:
        table.add_row(
            platform["displayName"],
            platform["skillStatus"],
            _colorize_mcp_status(platform.get("mcpStatus", "n/a")),
            platform.get("mcpDetail", "") or "",
        )
    console.print(table)
    if api_auth_probe is not None:
        marker = _status_markup("ok", "green") if api_auth_probe.reachable else _status_markup("remove", "red")
        console.print(f"{marker} DecisionOps API auth (/v1/auth/me): {api_auth_probe.short_status()}")
    if mcp_reach_probe is not None:
        marker = _status_markup("ok", "green") if mcp_reach_probe.reachable else _status_markup("remove", "red")
        url_hint = f" [dim]({mcp_expected_url})[/dim]" if mcp_expected_url else ""
        console.print(f"{marker} DecisionOps MCP server{url_hint}: {mcp_reach_probe.short_status()}")
    if mcp_gate_probe is not None:
        marker = _status_markup("ok", "green") if mcp_gate_probe.authenticated else _status_markup("remove", "red")
        console.print(f"{marker} DecisionOps MCP gate probe: {mcp_gate_probe.short_status()}")
        if mcp_gate_probe.project_resolution == "fallback_default":
            suggested = mcp_gate_probe.suggested_project_name or "repo project"
            console.print(
                f"[dim]  {_status_symbol('next')} Repo uses Default Project; coding agents will offer to create '{suggested}' on the next decision gate (and update .decisionops/manifest.toml).[/dim]"
            )
        elif mcp_gate_probe.project_resolution == "mapped_repo":
            console.print(
                f"[dim]  {_status_symbol('ok')} Repo is mapped to dedicated project `{mcp_gate_probe.resolved_project_id}`; coding agents will not prompt for project creation.[/dim]"
            )
    if issues:
        console.print(f"[yellow]{len(issues)} issue{'s' if len(issues) != 1 else ''} found:[/yellow]")
        for issue in issues:
            console.print(f"  - {issue}")
    else:
        console.print("[green]No issues found.[/green]")


def render_auth_status(auth) -> None:
    table = Table(box=box.SIMPLE)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Auth", "configured")
    table.add_row("API base URL", auth.apiBaseUrl)
    table.add_row("Issuer URL", auth.issuerUrl)
    table.add_row("Client ID", auth.clientId)
    table.add_row("Method", auth.method)
    table.add_row("Scopes", " ".join(auth.scopes))
    table.add_row("Access token", f"{auth.accessToken[:8]}…")
    table.add_row("Expires", auth.expiresAt or "session")
    if auth.user:
        table.add_row("User", auth.user.get("email") or auth.user.get("name") or auth.user.get("id") or "")
    console.print(table)
