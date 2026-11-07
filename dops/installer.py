from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DEFAULT_MCP_SERVER_NAME,
    DEFAULT_MCP_SERVER_URL,
    DEFAULT_SKILL_NAME,
    PLACEHOLDER_ORG_ID,
    PLACEHOLDER_PROJECT_ID,
    PLACEHOLDER_REPO_REF,
)
from .fileio import atomic_copy_dir, atomic_write_text
from .git import infer_default_branch, infer_repo_ref
from .manifest import InvalidManifestError, read_manifest, write_manifest
from .platforms import (
    PlatformDefinition,
    auth_instructions,
    context_for_paths,
    expand_path,
    load_platforms,
    resolve_install_path,
    select_platforms,
)


@dataclass(slots=True)
class InstallResult:
    built_platforms: list[str] = field(default_factory=list)
    installed_skills: list[dict[str, str]] = field(default_factory=list)
    installed_mcp: list[dict[str, str]] = field(default_factory=list)
    skipped_mcp: list[dict[str, str]] = field(default_factory=list)
    manifest_path: str | None = None
    placeholders_used: bool = False


@dataclass(slots=True)
class CleanupResult:
    removed_skills: list[dict[str, str]] = field(default_factory=list)
    skipped_skills: list[dict[str, str]] = field(default_factory=list)
    removed_mcp: list[dict[str, str]] = field(default_factory=list)
    skipped_mcp: list[dict[str, str]] = field(default_factory=list)
    removed_manifest_path: str | None = None


def _ensure_skill_source(source_dir: str) -> None:
    if not (Path(source_dir) / "SKILL.md").exists():
        raise RuntimeError(f"Skill source missing SKILL.md: {source_dir}")


def _ensure_parent(file_path: str) -> None:
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)


def _copy_dir(source_dir: str, target_dir: str) -> None:
    atomic_copy_dir(source_dir, target_dir)


_SAFE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_toml_key(value: str, label: str) -> None:
    if not _SAFE_TOML_KEY.match(value):
        raise RuntimeError(f"Invalid {label}: must contain only alphanumeric characters, hyphens, and underscores.")


def _render_mcp_build_content(platform: PlatformDefinition, server_name: str, server_url: str) -> str:
    mcp = platform.mcp
    if mcp is None or not mcp.format:
        raise RuntimeError(f"Platform '{platform.id}' is missing MCP format")
    if mcp.format == "codex_toml":
        # Match Codex's native HTTP-server shape — `url = "..."` only. See
        # _upsert_codex_toml for why extra fields get dropped.
        return f'[mcp_servers.{server_name}]\nurl = "{server_url}"\n'
    if mcp.format == "json_map":
        return json.dumps({mcp.root_key or "mcpServers": {server_name: {"type": "http", "url": server_url}}}, indent=2) + "\n"
    raise RuntimeError(f"Unsupported MCP format '{mcp.format}' for {platform.id}")


def _upsert_codex_toml(config_path: str, server_name: str, server_url: str) -> None:
    _validate_toml_key(server_name, "MCP server name")
    # Codex's MCP schema for HTTP/remote servers is just `url = "..."`. Writing
    # extra keys like `type` or `enabled` causes Codex to silently drop the
    # whole entry when the UI saves settings (observed behavior — it rewrites
    # config.toml and strips entries with unknown fields). Match the shape of
    # existing HTTP entries (see `notion`, `openaiDeveloperDocs`).
    section_header = f"[mcp_servers.{server_name}]"
    new_block = [section_header, f'url = "{server_url}"']
    file_path = Path(config_path)
    try:
        lines = file_path.read_text(encoding="utf8").splitlines() if file_path.exists() else []
    except (UnicodeDecodeError, OSError) as error:
        raise RuntimeError(f"Cannot read MCP config {config_path}: {error}") from error
    output: list[str] = []
    inserted = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == section_header:
            if not inserted:
                output.extend(new_block)
                inserted = True
            index += 1
            while index < len(lines) and not lines[index].startswith("["):
                index += 1
            continue
        output.append(line)
        index += 1
    if not inserted:
        if output and output[-1] != "":
            output.append("")
        output.extend(new_block)
    _ensure_parent(config_path)
    atomic_write_text(file_path, "\n".join(output).rstrip("\n") + "\n", encoding="utf8")


def _upsert_json_map(config_path: str, root_key: str, server_name: str, server_url: str) -> None:
    file_path = Path(config_path)
    try:
        raw = file_path.read_text(encoding="utf8")
    except FileNotFoundError:
        raw = ""
    except (UnicodeDecodeError, OSError) as error:
        raise RuntimeError(f"Cannot read MCP config {config_path}: {error}") from error
    if not raw.strip():
        data = {}
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid JSON in MCP config {config_path}: {error}") from error
    root = data.get(root_key, {})
    if not isinstance(root, dict):
        root = {}
    root[server_name] = {"type": "http", "url": server_url}
    data[root_key] = root
    _ensure_parent(config_path)
    atomic_write_text(file_path, json.dumps(data, indent=2) + "\n", encoding="utf8")


def _remove_codex_toml_server(config_path: str, server_name: str) -> bool:
    file_path = Path(config_path)
    if not file_path.exists():
        return False
    section_header = re.compile(rf"^\[mcp_servers\.{re.escape(server_name)}\]\s*$")
    try:
        lines = file_path.read_text(encoding="utf8").splitlines()
    except (UnicodeDecodeError, OSError) as error:
        raise RuntimeError(f"Cannot read MCP config {config_path}: {error}") from error
    output: list[str] = []
    removed = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if section_header.match(line.strip()):
            removed = True
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("["):
                index += 1
            continue
        output.append(line)
        index += 1
    if not removed:
        return False
    normalized = "\n".join(output).rstrip("\n")
    if not normalized.strip():
        file_path.unlink(missing_ok=True)
        return True
    _ensure_parent(config_path)
    atomic_write_text(file_path, normalized + "\n", encoding="utf8")
    return True


def _remove_json_map_server(config_path: str, root_key: str, server_name: str) -> bool:
    file_path = Path(config_path)
    if not file_path.exists():
        return False
    try:
        raw = file_path.read_text(encoding="utf8").strip()
    except (UnicodeDecodeError, OSError) as error:
        raise RuntimeError(f"Cannot read MCP config {config_path}: {error}") from error
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON in MCP config {config_path}: {error}") from error
    root = data.get(root_key)
    if not isinstance(root, dict) or server_name not in root:
        return False
    del root[server_name]
    if not root:
        del data[root_key]
    if not data:
        file_path.unlink(missing_ok=True)
        return True
    _ensure_parent(config_path)
    atomic_write_text(file_path, json.dumps(data, indent=2) + "\n", encoding="utf8")
    return True


def _remove_file_if_present(file_path: str) -> bool:
    path = Path(file_path)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def _remove_empty_dir_if_present(dir_path: str) -> None:
    path = Path(dir_path)
    try:
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def build_platform(
    platform: PlatformDefinition,
    skill_name: str,
    source_dir: str,
    output_dir: str,
    server_name: str,
    server_url: str,
) -> str:
    platform_output = Path(output_dir) / platform.id
    if platform_output.exists():
        try:
            shutil.rmtree(platform_output)
        except OSError as error:
            raise RuntimeError(f"Could not clear existing build output at {platform_output}: {error}") from error
    context = context_for_paths(skill_name, None)
    if platform.skill and platform.skill.supported and platform.skill.build_path:
        skill_build_path = expand_path(platform.skill.build_path, context)
        _copy_dir(source_dir, str(platform_output / skill_build_path))
    if platform.mcp and platform.mcp.supported and platform.mcp.build_path:
        mcp_build_path = expand_path(platform.mcp.build_path, context)
        target_path = platform_output / mcp_build_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target_path, _render_mcp_build_content(platform, server_name, server_url), encoding="utf8")
    if platform.manifest and platform.manifest.supported and platform.manifest.build_path:
        manifest_build_path = expand_path(platform.manifest.build_path, context)
        target_path = platform_output / manifest_build_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target_path,
            "\n".join(
                [
                    "version = 1",
                    f'org_id = "{PLACEHOLDER_ORG_ID}"',
                    f'project_id = "{PLACEHOLDER_PROJECT_ID}"',
                    f'repo_ref = "{PLACEHOLDER_REPO_REF}"',
                    'default_branch = "main"',
                    f'mcp_server_name = "{server_name}"',
                    f'mcp_server_url = "{server_url}"',
                    "",
                ]
            ),
            encoding="utf8",
        )
    return str(platform_output)


def build_platforms(options: dict[str, object]) -> list[dict[str, str]]:
    platforms = load_platforms(str(options["platforms_dir"]))
    selected = select_platforms(platforms, options.get("selected_platforms"))  # type: ignore[arg-type]
    skill_name = str(options.get("skill_name") or DEFAULT_SKILL_NAME)
    source_dir = str(options.get("source_dir") or "")
    output_dir = str(options.get("output_dir") or "")
    server_name = str(options.get("server_name") or DEFAULT_MCP_SERVER_NAME)
    server_url = str(options.get("server_url") or DEFAULT_MCP_SERVER_URL)
    if source_dir:
        _ensure_skill_source(source_dir)
    return [
        {
            "platform_id": platform.id,
            "output_path": build_platform(platform, skill_name, source_dir, output_dir, server_name, server_url),
        }
        for platform in selected
    ]


def install_platforms(options: dict[str, object]) -> InstallResult:
    platforms = load_platforms(str(options["platforms_dir"]))
    selected = select_platforms(platforms, options.get("selected_platforms"))  # type: ignore[arg-type]
    skill_name = str(options.get("skill_name") or DEFAULT_SKILL_NAME)
    source_dir = str(options.get("source_dir") or "")
    output_dir = str(options.get("output_dir") or "")
    server_name = str(options.get("server_name") or DEFAULT_MCP_SERVER_NAME)
    server_url = str(options.get("server_url") or DEFAULT_MCP_SERVER_URL)
    repo_path = str(options["repo_path"]) if options.get("repo_path") else None
    install_skill = bool(options.get("install_skill", True))
    install_mcp = bool(options.get("install_mcp", True))
    should_write_manifest = bool(options.get("write_manifest", True))
    allow_placeholders = bool(options.get("allow_placeholders", False))
    existing_manifest: dict[str, object] | None = None
    if source_dir:
        _ensure_skill_source(source_dir)
    if not source_dir and install_skill and any(platform.skill and platform.skill.supported for platform in selected):
        raise RuntimeError("Skill source is required to install skill files. Pass --source-dir or use --skip-skill.")
    repo_required = should_write_manifest or (
        install_mcp and any(platform.mcp and platform.mcp.supported and platform.mcp.scope == "project" for platform in selected)
    )
    if repo_required and not repo_path:
        raise RuntimeError("--repo-path is required for manifest writes or project-scoped MCP config.")
    if should_write_manifest and repo_path:
        try:
            existing_manifest = read_manifest(repo_path)
        except InvalidManifestError as error:
            raise RuntimeError(
                f"{error} Or pass --skip-manifest to leave the current file untouched."
            ) from error
        if existing_manifest:
            if not options.get("server_name") and existing_manifest.get("mcp_server_name"):
                server_name = str(existing_manifest["mcp_server_name"])
            if not options.get("server_url") and existing_manifest.get("mcp_server_url"):
                server_url = str(existing_manifest["mcp_server_url"])
    result = InstallResult()
    if should_write_manifest and repo_path:
        org_id = options.get("org_id") or (existing_manifest.get("org_id") if existing_manifest else None)
        if not org_id:
            if allow_placeholders:
                result.placeholders_used = True
                org_id = PLACEHOLDER_ORG_ID
            else:
                raise RuntimeError("--org-id is required when writing a manifest.")
        project_id = options.get("project_id") or (existing_manifest.get("project_id") if existing_manifest else None)
        if not project_id:
            if allow_placeholders:
                result.placeholders_used = True
                project_id = PLACEHOLDER_PROJECT_ID
            else:
                raise RuntimeError("--project-id is required when writing a manifest.")
        repo_ref = options.get("repo_ref") or (existing_manifest.get("repo_ref") if existing_manifest else None)
        if not repo_ref:
            try:
                repo_ref = infer_repo_ref(repo_path)
            except RuntimeError:
                if allow_placeholders:
                    result.placeholders_used = True
                    repo_ref = PLACEHOLDER_REPO_REF
                else:
                    raise RuntimeError("Could not infer repo_ref from git remote.")
        default_branch = str(
            options.get("default_branch")
            or (existing_manifest.get("default_branch") if existing_manifest else None)
            or infer_default_branch(repo_path)
        )
        result.manifest_path = write_manifest(
            repo_path,
            {
                "org_id": str(org_id),
                "project_id": str(project_id),
                "repo_ref": str(repo_ref),
                "repo_id": (
                    str(options["repo_id"])
                    if options.get("repo_id")
                    else (str(existing_manifest["repo_id"]) if existing_manifest and existing_manifest.get("repo_id") else None)
                ),
                "default_branch": default_branch,
                "mcp_server_name": server_name,
                "mcp_server_url": server_url,
            },
        )
    if source_dir and output_dir:
        for entry in build_platforms(
            {
                "platforms_dir": options["platforms_dir"],
                "selected_platforms": options.get("selected_platforms"),
                "skill_name": skill_name,
                "source_dir": source_dir,
                "output_dir": output_dir,
                "server_name": server_name,
                "server_url": server_url,
            }
        ):
            result.built_platforms.append(entry["platform_id"])
    for platform in selected:
        context = context_for_paths(skill_name, repo_path)
        if install_skill and platform.skill and platform.skill.supported and source_dir:
            target = resolve_install_path(platform.skill, context)
            if not target:
                raise RuntimeError(f"Could not determine skill install path for {platform.id}")
            if output_dir:
                relative_path = expand_path(platform.skill.build_path or "", context_for_paths(skill_name, None))
                bundle = str(Path(output_dir) / platform.id / relative_path)
                _copy_dir(bundle, target)
            else:
                _copy_dir(source_dir, target)
            result.installed_skills.append({"platformId": platform.id, "target": target})
        if install_mcp and platform.mcp and platform.mcp.supported:
            target = resolve_install_path(platform.mcp, context)
            if not target:
                result.skipped_mcp.append({"platformId": platform.id, "reason": "no target path configured"})
                continue
            if platform.mcp.format == "codex_toml":
                _upsert_codex_toml(target, server_name, server_url)
            elif platform.mcp.format == "json_map":
                _upsert_json_map(target, platform.mcp.root_key or "mcpServers", server_name, server_url)
            else:
                raise RuntimeError(f"Unsupported MCP format '{platform.mcp.format}' for {platform.id}")
            auth_steps = auth_instructions(
                platform,
                {
                    "display_name": platform.display_name,
                    "mcp_server_name": server_name,
                    "mcp_server_url": server_url,
                },
            ) or []
            result.installed_mcp.append(
                {
                    "platformId": platform.id,
                    "displayName": platform.display_name,
                    "target": target,
                    "authSteps": auth_steps,
                }
            )
    return result


def cleanup_platforms(options: dict[str, object]) -> CleanupResult:
    platforms = load_platforms(str(options["platforms_dir"]))
    selected = select_platforms(platforms, options.get("selected_platforms"))  # type: ignore[arg-type]
    skill_name = str(options.get("skill_name") or DEFAULT_SKILL_NAME)
    repo_path = str(options["repo_path"]) if options.get("repo_path") else None
    remove_skill = bool(options.get("remove_skill", True))
    remove_mcp = bool(options.get("remove_mcp", True))
    server_name = str(options.get("server_name") or DEFAULT_MCP_SERVER_NAME)
    result = CleanupResult()
    for platform in selected:
        context = context_for_paths(skill_name, repo_path)
        if remove_skill and platform.skill and platform.skill.supported:
            target = resolve_install_path(platform.skill, context)
            if not target:
                result.skipped_skills.append({"platformId": platform.id, "reason": "no target path configured"})
            elif not Path(target).exists():
                result.skipped_skills.append({"platformId": platform.id, "reason": f"skill path does not exist ({target})"})
            else:
                shutil.rmtree(target, ignore_errors=True)
                result.removed_skills.append({"platformId": platform.id, "target": target})
        if remove_mcp and platform.mcp and platform.mcp.supported:
            target = resolve_install_path(platform.mcp, context)
            if not target:
                result.skipped_mcp.append({"platformId": platform.id, "reason": "no target path configured"})
                continue
            try:
                removed = False
                if platform.mcp.format == "codex_toml":
                    removed = _remove_codex_toml_server(target, server_name)
                elif platform.mcp.format == "json_map":
                    removed = _remove_json_map_server(target, platform.mcp.root_key or "mcpServers", server_name)
                else:
                    result.skipped_mcp.append({"platformId": platform.id, "reason": f"unsupported MCP format '{platform.mcp.format}'"})
                    continue
                if removed:
                    result.removed_mcp.append({"platformId": platform.id, "target": target})
                elif not Path(target).exists():
                    result.skipped_mcp.append({"platformId": platform.id, "reason": f"config path does not exist ({target})"})
                else:
                    result.skipped_mcp.append({"platformId": platform.id, "reason": f"server '{server_name}' not found"})
            except (OSError, RuntimeError) as error:  # pragma: no cover - defensive
                result.skipped_mcp.append({"platformId": platform.id, "reason": str(error)})
    if repo_path and bool(options.get("remove_manifest", False)):
        manifest_path = str(Path(repo_path) / ".decisionops" / "manifest.toml")
        if _remove_file_if_present(manifest_path):
            result.removed_manifest_path = manifest_path
    if repo_path:
        _remove_empty_dir_if_present(str(Path(repo_path) / ".decisionops"))
    return result
