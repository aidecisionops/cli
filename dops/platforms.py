from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from .config import expand_home
from .generated.platform_models import (
    PlatformAuthSpec,
    PlatformInstallSpec,
)
from .generated.platform_models import (
    PlatformDefinition as _BasePlatformDefinition,
)
from .text_utils import levenshtein_distance


class InvalidPlatformDefinitionError(RuntimeError):
    def __init__(self, file_path: Path, details: str) -> None:
        self.file_path = file_path
        self.details = details
        super().__init__(
            f"DecisionOps platform definition is invalid: {file_path}: {details}. "
            "Refresh the skill bundle or provide a valid local skill checkout."
        )


class PlatformDefinition(_BasePlatformDefinition):
    """Extends the generated PlatformDefinition with runtime-only file_path field."""

    file_path: str = ""


def format_template(template: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise RuntimeError(f"Missing template variable '{key}' in value: {template}")
        return context[key]

    return re.sub(r"\{([^}]+)\}", replace, template)


def expand_path(value: str, context: dict[str, str]) -> str:
    return expand_home(format_template(value, context))


def context_for_paths(skill_name: str, repo_path: str | None) -> dict[str, str]:
    return {"skill_name": skill_name, "repo_path": repo_path or ""}


def _install_spec(data: dict[str, object] | None) -> PlatformInstallSpec | None:
    if data is None:
        return None
    return PlatformInstallSpec.model_validate(data)


def _auth_spec(data: dict[str, object] | None) -> PlatformAuthSpec | None:
    if data is None:
        return None
    return PlatformAuthSpec.model_validate(data)


def load_platforms(platforms_dir: str) -> dict[str, PlatformDefinition]:
    platforms: dict[str, PlatformDefinition] = {}
    for file_path in sorted(Path(platforms_dir).glob("*.toml")):
        try:
            parsed = tomllib.loads(file_path.read_text(encoding="utf8"))
        except tomllib.TOMLDecodeError as error:
            raise InvalidPlatformDefinitionError(file_path, str(error)) from error
        platform_id = str(parsed.get("id", ""))
        if not platform_id:
            raise RuntimeError(f"Platform file missing id: {file_path}")
        if platform_id != file_path.stem:
            raise RuntimeError(f"Platform id '{platform_id}' must match filename: {file_path}")
        platforms[platform_id] = PlatformDefinition(
            id=platform_id,
            display_name=str(parsed.get("display_name", platform_id)),
            skill=_install_spec(parsed.get("skill") if isinstance(parsed.get("skill"), dict) else None),
            mcp=_install_spec(parsed.get("mcp") if isinstance(parsed.get("mcp"), dict) else None),
            manifest=_install_spec(parsed.get("manifest") if isinstance(parsed.get("manifest"), dict) else None),
            auth=_auth_spec(parsed.get("auth") if isinstance(parsed.get("auth"), dict) else None),
            file_path=str(file_path),
        )
    if not platforms:
        raise RuntimeError(f"No platform definitions found in {platforms_dir}")
    return platforms


def _normalize_platform_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _suggest_platform_id(platform_ids: list[str], input_value: str) -> str | None:
    normalized_input = _normalize_platform_id(input_value)
    if not normalized_input:
        return None
    best_prefix_match: str | None = None
    best_distance_match: tuple[str, int] | None = None
    for platform_id in platform_ids:
        normalized_platform_id = _normalize_platform_id(platform_id)
        if (
            normalized_platform_id.startswith(normalized_input)
            or normalized_input.startswith(normalized_platform_id)
            or normalized_input in normalized_platform_id
        ):
            if best_prefix_match is None or len(platform_id) < len(best_prefix_match):
                best_prefix_match = platform_id
            continue
        distance = levenshtein_distance(normalized_input, normalized_platform_id)
        if best_distance_match is None or distance < best_distance_match[1]:
            best_distance_match = (platform_id, distance)
    if best_prefix_match:
        return best_prefix_match
    if best_distance_match is None:
        return None
    max_distance = 1 if len(normalized_input) <= 4 else 2
    return best_distance_match[0] if best_distance_match[1] <= max_distance else None


def _unknown_platforms_message(platform_ids: list[str], missing: list[str]) -> str:
    base = f"Unknown platform(s): {', '.join(missing)}."
    if len(missing) == 1:
        suggestion = _suggest_platform_id(platform_ids, missing[0])
        if suggestion:
            return f"{base} Did you mean '{suggestion}'? Run 'dops platform list' for supported platforms."
        return f"{base} Run 'dops platform list' for supported platforms."
    suggestions = []
    for item in missing:
        suggestion = _suggest_platform_id(platform_ids, item)
        if suggestion:
            suggestions.append(f"'{item}' -> '{suggestion}'")
    if suggestions:
        return f"{base} Suggestions: {', '.join(suggestions)}. Run 'dops platform list' for supported platforms."
    return f"{base} Run 'dops platform list' for supported platforms."


def select_platforms(
    platforms: dict[str, PlatformDefinition],
    selected_ids: list[str] | None = None,
    capability: str | None = None,
) -> list[PlatformDefinition]:
    ordered_ids = selected_ids if selected_ids else list(platforms.keys())
    missing = [platform_id for platform_id in ordered_ids if platform_id not in platforms]
    if missing:
        raise RuntimeError(_unknown_platforms_message(list(platforms.keys()), missing))
    selected = [platforms[platform_id] for platform_id in ordered_ids]
    if capability is None:
        return selected
    return [
        platform
        for platform in selected
        if getattr(platform, capability) is not None and bool(getattr(getattr(platform, capability), "supported", False))
    ]


def resolve_install_path(spec: PlatformInstallSpec, context: dict[str, str]) -> str | None:
    if spec.install_path_env and os.environ.get(spec.install_path_env):
        return str(Path(expand_path(os.environ[spec.install_path_env], context)).resolve())
    if spec.install_root_env or spec.install_root_default:
        root_value = (
            os.environ.get(spec.install_root_env, spec.install_root_default or "")
            if spec.install_root_env
            else spec.install_root_default
        )
        if not root_value:
            return None
        root_path = Path(expand_path(root_value, context)).resolve()
        return str(root_path / format_template(spec.install_path_suffix or "", context))
    if spec.install_path_default:
        if "{repo_path}" in spec.install_path_default and not context.get("repo_path"):
            return None
        return str(Path(expand_path(spec.install_path_default, context)).resolve())
    return None


def auth_instructions(platform: PlatformDefinition, context: dict[str, str]) -> list[str] | None:
    # Any auth mode that carries user-facing instructions should return them
    # (browser_oauth, interactive_handoff, etc.). `instructions` being empty
    # is the actual signal that no prompt is needed.
    if platform.auth is None or not platform.auth.instructions:
        return None
    return [format_template(step, context) for step in platform.auth.instructions]
