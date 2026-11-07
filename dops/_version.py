from __future__ import annotations

import subprocess
from importlib import metadata
from pathlib import Path
import re

DEFAULT_VERSION = "0.1.27"
_SEMVER_LIKE_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$")


def _normalize_version(value: str) -> str:
    normalized = value.strip()
    return normalized[1:] if normalized.startswith("v") else normalized


def _version_from_build_file() -> str | None:
    try:
        from ._build_version import __version__ as build_version
    except ImportError:
        return None
    return _normalize_version(build_version)


def _version_from_metadata() -> str | None:
    try:
        return _normalize_version(metadata.version("decisionops-dops"))
    except metadata.PackageNotFoundError:
        return None


def _version_from_git() -> str | None:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    if not value:
        return None
    normalized = _normalize_version(value)
    return normalized if _SEMVER_LIKE_PATTERN.match(normalized) else None


def resolve_version() -> str:
    return _version_from_build_file() or _version_from_git() or _version_from_metadata() or DEFAULT_VERSION


__version__ = resolve_version()
