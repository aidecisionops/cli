"""Execute and describe per-platform MCP auth triggers.

The platform catalog declares typed `AuthTrigger` entries. `kind=cli`
triggers are shell commands dops can run for the user (e.g.
`codex mcp login decision-ops-mcp`). Other kinds are human-readable
hints (slash commands, command-palette paths, or manual UI steps) for
IDEs that don't expose a CLI entry point. Extensibility story: adding
a new IDE is a platform TOML file — no CLI code change needed unless
the IDE introduces a brand-new trigger kind.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .generated.platform_models import AuthTrigger
from .platforms import PlatformDefinition, format_template

# Interactive OAuth means the user has to click "Authorize" in a browser —
# could take a while on slow networks or if the user gets distracted. 15
# minutes is generous enough for humans but still bounded so a frozen IDE
# subprocess eventually frees.
CLI_TRIGGER_TIMEOUT_SECONDS = 900


@dataclass
class TriggerExecutionResult:
    trigger: AuthTrigger
    status: str  # "ran" | "failed" | "described"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ran", "described")


def _render_trigger(trigger: AuthTrigger, context: dict[str, str]) -> AuthTrigger:
    """Apply template substitution to all string fields of a trigger.

    The TOML stores templated strings (e.g. `{mcp_server_name}`) so the
    same definition works across orgs and projects. Render once at call
    time with the active context.
    """
    return AuthTrigger(
        kind=trigger.kind,
        reason=trigger.reason,
        label=format_template(trigger.label, context) if trigger.label else None,
        hint=format_template(trigger.hint, context) if trigger.hint else None,
        command=[format_template(part, context) for part in trigger.command]
        if trigger.command
        else None,
    )


def platform_triggers(platform: PlatformDefinition, context: dict[str, str]) -> list[AuthTrigger]:
    """Return the platform's rendered triggers, or an empty list if none declared."""
    if platform.auth is None or not platform.auth.triggers:
        return []
    return [_render_trigger(t, context) for t in platform.auth.triggers]


def describe_trigger(trigger: AuthTrigger) -> str:
    """Return a one-line human-readable summary of a trigger.

    CLI triggers render as their literal argv joined with spaces so
    users can paste or verify. Non-CLI triggers use their `hint` or
    `label` as written — the TOML has already been templated.
    """
    if trigger.kind == "cli" and trigger.command:
        return " ".join(trigger.command)
    return trigger.hint or trigger.label or f"({trigger.kind} trigger with no hint)"


def execute_cli_trigger(trigger: AuthTrigger) -> TriggerExecutionResult:
    """Run a `kind=cli` trigger via subprocess.

    Streams stdout/stderr to the current TTY so OAuth prompts (e.g.
    Codex printing a localhost callback URL) are visible. Returns a
    structured result so callers can decide what to render.
    """
    if trigger.kind != "cli":
        return TriggerExecutionResult(trigger=trigger, status="described", detail=describe_trigger(trigger))
    if not trigger.command:
        return TriggerExecutionResult(
            trigger=trigger,
            status="failed",
            detail="cli trigger has no command argv",
        )
    binary = trigger.command[0]
    if shutil.which(binary) is None:
        return TriggerExecutionResult(
            trigger=trigger,
            status="failed",
            detail=f"`{binary}` is not on PATH; install it first",
        )
    try:
        completed = subprocess.run(
            list(trigger.command),
            check=False,
            timeout=CLI_TRIGGER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return TriggerExecutionResult(
            trigger=trigger,
            status="failed",
            detail=f"`{' '.join(trigger.command)}` timed out after {CLI_TRIGGER_TIMEOUT_SECONDS}s",
        )
    except OSError as error:
        return TriggerExecutionResult(
            trigger=trigger,
            status="failed",
            detail=f"failed to run `{' '.join(trigger.command)}`: {error}",
        )
    if completed.returncode != 0:
        return TriggerExecutionResult(
            trigger=trigger,
            status="failed",
            detail=f"`{' '.join(trigger.command)}` exited {completed.returncode}",
        )
    return TriggerExecutionResult(trigger=trigger, status="ran", detail=" ".join(trigger.command))


def triggers_by_reason(triggers: list[AuthTrigger], reason: str) -> list[AuthTrigger]:
    """Filter triggers matching a given reason (primary/reset), preserving declared order."""
    return [t for t in triggers if (t.reason or "primary") == reason]
