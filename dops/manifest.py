from __future__ import annotations

import tomllib
import shutil
import uuid
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.exceptions import ParseError

from .fileio import atomic_write_text


class InvalidManifestError(RuntimeError):
    def __init__(self, file_path: Path, details: str) -> None:
        self.file_path = file_path
        self.details = details
        super().__init__(
            f"Repository manifest is invalid: {file_path}: {details}. "
            "Run `dops init` interactively to repair the binding."
        )


def _decisionops_dir(repo_path: str) -> Path:
    return Path(repo_path) / ".decisionops"


def write_manifest(repo_path: str, values: dict[str, str | None], *, replace_invalid: bool = False) -> str:
    file_path = _decisionops_dir(repo_path) / "manifest.toml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        document = tomlkit.parse(file_path.read_text(encoding="utf8")) if file_path.exists() else tomlkit.document()
    except (ParseError, OSError, UnicodeError) as error:
        if not replace_invalid:
            raise InvalidManifestError(file_path, str(error)) from error
        shutil.copyfile(file_path, file_path.with_name(f"manifest.toml.invalid-backup-{uuid.uuid4().hex}"))
        document = tomlkit.document()
    if document.get("version", 1) != 1:
        raise InvalidManifestError(file_path, "unsupported manifest version; expected 1")
    changed_repo = any(document.get(key) not in (None, values[key]) for key in ("org_id", "repo_ref"))
    document.setdefault("version", 1)
    for key, value in values.items():
        if value is not None:
            document[key] = str(value)
    if changed_repo and not values.get("repo_id"):
        document.pop("repo_id", None)
    atomic_write_text(file_path, tomlkit.dumps(document), encoding="utf8")
    return str(file_path)


def read_manifest(repo_path: str) -> dict[str, Any] | None:
    file_path = _decisionops_dir(repo_path) / "manifest.toml"
    if not file_path.exists():
        return None
    try:
        return tomllib.loads(file_path.read_text(encoding="utf8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeError) as error:
        raise InvalidManifestError(file_path, str(error)) from error


def manifest_summary(repo_path: str) -> dict[str, Any]:
    values = read_manifest(repo_path)
    file_path = _decisionops_dir(repo_path) / "manifest.toml"
    if values is None:
        raise RuntimeError(f"Manifest not found: {file_path}")
    if values.get("version", 1) != 1:
        raise InvalidManifestError(file_path, "unsupported manifest version; expected 1")
    for key in ("org_id", "project_id", "repo_ref"):
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise InvalidManifestError(file_path, f"missing or invalid required string: {key}")
    return {"manifest_path": str(file_path.resolve()), **{key: values.get(key, "") for key in ("org_id", "project_id", "repo_ref", "repo_id", "default_branch", "mcp_server_name", "mcp_server_url")}}
