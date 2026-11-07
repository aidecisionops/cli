from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .config import DEFAULT_SKILL_REPO_REF, DEFAULT_SKILL_REPO_URL, decisionops_home
from .fileio import atomic_copy_dir, atomic_write_text
from .http import default_user_agent, urlopen_with_retries
from .tls import create_ssl_context


def _directory_has_toml_files(dir_path: Path) -> bool:
    try:
        return dir_path.is_dir() and any(entry.suffix == ".toml" for entry in dir_path.iterdir())
    except OSError:
        return False


def _is_skill_bundle_dir(dir_path: Path) -> bool:
    try:
        return dir_path.is_dir() and (dir_path / "SKILL.md").exists()
    except OSError:
        return False


def _ancestor_dirs(start: str) -> list[Path]:
    dirs: list[Path] = []
    current = Path(start).resolve()
    while True:
        dirs.append(current)
        if current.parent == current:
            break
        current = current.parent
    return dirs


def _search_roots(overrides: list[str] | None = None) -> list[Path]:
    if overrides:
        return [Path(item).resolve() for item in overrides]
    defaults = [
        Path(__file__).resolve().parent,
        Path.cwd(),
        Path(os.path.dirname(os.path.realpath(sys.executable))),
    ]
    unique: list[Path] = []
    for item in defaults:
        if item not in unique:
            unique.append(item)
    return unique


def _find_resource_dir(
    candidates: list[tuple[str, ...]],
    matcher,
    error_message: str,
    roots: list[str] | None = None,
) -> str:
    for root in _search_roots(roots):
        for base in _ancestor_dirs(str(root)):
            for segments in candidates:
                candidate = base.joinpath(*segments)
                if matcher(candidate):
                    return str(candidate)
    raise RuntimeError(error_message)


def _resources_root() -> Path:
    return Path(decisionops_home()) / "resources"


def _skill_repo_root() -> Path:
    return _resources_root() / "skill-repo"


def _skill_repo_cache_dir() -> Path:
    return _skill_repo_root() / "repo"


def _skill_repo_manifest_path() -> Path:
    return _skill_repo_root() / "manifest.json"


def _is_skill_repo_dir(dir_path: Path) -> bool:
    return _directory_has_toml_files(dir_path / "platforms") and _is_skill_bundle_dir(dir_path / "decision-ops")


def _read_skill_repo_manifest() -> dict[str, str] | None:
    manifest_path = _skill_repo_manifest_path()
    if not manifest_path.exists():
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    repo_url = raw.get("repo_url")
    ref = raw.get("ref")
    if not isinstance(repo_url, str) or not isinstance(ref, str):
        return None
    return {"repo_url": repo_url, "ref": ref}


def _github_archive_url(repo_url: str, ref: str) -> str:
    normalized = repo_url.strip().rstrip("/").removesuffix(".git")
    repo_path: str | None = None
    if normalized.startswith("https://github.com/"):
        repo_path = normalized.removeprefix("https://github.com/")
    elif normalized.startswith("http://github.com/"):
        repo_path = normalized.removeprefix("http://github.com/")
    elif normalized.startswith("git@github.com:"):
        repo_path = normalized.removeprefix("git@github.com:")
    elif normalized.startswith("ssh://git@github.com/"):
        repo_path = normalized.removeprefix("ssh://git@github.com/")
    if not repo_path or repo_path.count("/") != 1:
        raise RuntimeError(
            "Could not derive a downloadable archive URL for the DecisionOps skill repository. "
            "Set DECISIONOPS_SKILL_REPO_URL to a GitHub repository URL."
        )
    return f"https://codeload.github.com/{repo_path}/zip/refs/heads/{urllib.parse.quote(ref, safe='')}"


def _write_skill_repo_manifest(repo_dir: Path, *, repo_url: str, ref: str, archive_url: str) -> None:
    atomic_write_text(
        _skill_repo_manifest_path(),
        json.dumps(
            {
                "repo_url": repo_url,
                "ref": ref,
                "archive_url": archive_url,
                "repo_dir": str(repo_dir),
            },
            indent=2,
        )
        + "\n",
        encoding="utf8",
    )


def _download_skill_repo(repo_url: str, ref: str) -> Path:
    archive_url = _github_archive_url(repo_url, ref)
    request = urllib.request.Request(archive_url, headers={"User-Agent": default_user_agent()})
    try:
        response = urlopen_with_retries(request, timeout=30, context=create_ssl_context())
    except Exception as error:  # pragma: no cover - exercised through user-facing error handling
        raise RuntimeError(f"Could not download DecisionOps skill resources from {repo_url}@{ref}: {error}") from error

    target_root = _skill_repo_root()
    target_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dops-skill-repo-") as temp_dir:
        extract_root = Path(temp_dir)
        try:
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                for member in archive.namelist():
                    resolved = (extract_root / member).resolve()
                    if not str(resolved).startswith(str(extract_root.resolve())):
                        raise RuntimeError(f"Zip archive contains path traversal entry: {member}")
                archive.extractall(extract_root)
        except zipfile.BadZipFile as error:
            raise RuntimeError(f"Downloaded DecisionOps skill archive was invalid: {error}") from error

        repo_dir: Path | None = None
        for candidate in extract_root.iterdir():
            if _is_skill_repo_dir(candidate):
                repo_dir = candidate
                break
            monorepo_skill_dir = candidate / "skill"
            if _is_skill_repo_dir(monorepo_skill_dir):
                repo_dir = monorepo_skill_dir
                break
        if repo_dir is None:
            raise RuntimeError(
                "Downloaded DecisionOps skill archive did not contain the expected `platforms/` and `decision-ops/` directories."
            )
        atomic_copy_dir(repo_dir, _skill_repo_cache_dir())
    _write_skill_repo_manifest(_skill_repo_cache_dir(), repo_url=repo_url, ref=ref, archive_url=archive_url)
    return _skill_repo_cache_dir()


def ensure_skill_repo_cache(*, refresh: bool = False) -> str:
    cache_dir = _skill_repo_cache_dir()
    expected_repo_url = DEFAULT_SKILL_REPO_URL
    expected_ref = DEFAULT_SKILL_REPO_REF
    manifest = _read_skill_repo_manifest()
    if (
        not refresh
        and _is_skill_repo_dir(cache_dir)
        and manifest is not None
        and manifest.get("repo_url") == expected_repo_url
        and manifest.get("ref") == expected_ref
    ):
        return str(cache_dir)
    return str(_download_skill_repo(expected_repo_url, expected_ref))


def resolve_local_skill_repo(source_dir: str) -> tuple[str, str]:
    source_path = Path(source_dir).expanduser().resolve()
    if _is_skill_repo_dir(source_path):
        return (str(source_path / "platforms"), str(source_path / "decision-ops"))
    if _is_skill_bundle_dir(source_path):
        repo_root = source_path.parent
        platforms_dir = repo_root / "platforms"
        if _directory_has_toml_files(platforms_dir):
            return (str(platforms_dir), str(source_path))
        raise RuntimeError(
            f"`--source-dir` points at a skill bundle ({source_path}) but no sibling `platforms/` directory was found."
        )
    raise RuntimeError(
        f"`--source-dir` must point to either a DecisionOps skill repo or a `decision-ops/` bundle directory. Got: {source_path}"
    )


def _find_local_platforms_dir(roots: list[str] | None = None) -> str:
    return _find_resource_dir(
        [
            ("node_modules", "@decisionops", "skill", "platforms"),
            ("skill", "platforms"),
            ("platforms",),
        ],
        _directory_has_toml_files,
        "Could not find local platform definitions.",
        roots,
    )


def _find_local_skill_source_dir(roots: list[str] | None = None) -> str:
    return _find_resource_dir(
        [
            ("node_modules", "@decisionops", "skill", "decision-ops"),
            ("skill", "decision-ops"),
            ("decision-ops",),
        ],
        _is_skill_bundle_dir,
        "Could not find local DecisionOps skill bundle.",
        roots,
    )


def find_platforms_dir(roots: list[str] | None = None) -> str:
    if roots:
        return _find_local_platforms_dir(roots)
    cache_error: str | None = None
    try:
        cached_repo = Path(ensure_skill_repo_cache())
        candidate = cached_repo / "platforms"
        if _directory_has_toml_files(candidate):
            return str(candidate)
    except RuntimeError as error:
        cache_error = str(error)
    try:
        return _find_local_platforms_dir()
    except RuntimeError:
        if cache_error:
            raise RuntimeError(
                f"Could not load DecisionOps platform definitions. {cache_error}"
            ) from None
        raise RuntimeError(
            "Could not load DecisionOps platform definitions. Run `dops install` with network access or provide a local skill checkout."
        ) from None


def find_skill_source_dir(roots: list[str] | None = None) -> str:
    if roots:
        return _find_local_skill_source_dir(roots)
    cache_error: str | None = None
    try:
        cached_repo = Path(ensure_skill_repo_cache())
        candidate = cached_repo / "decision-ops"
        if _is_skill_bundle_dir(candidate):
            return str(candidate)
    except RuntimeError as error:
        cache_error = str(error)
    try:
        return _find_local_skill_source_dir()
    except RuntimeError:
        if cache_error:
            raise RuntimeError(
                f"Could not load the DecisionOps skill bundle. {cache_error}"
            ) from None
        raise RuntimeError(
            "Could not load the DecisionOps skill bundle. Run `dops install` with network access or provide a local skill checkout."
        ) from None
