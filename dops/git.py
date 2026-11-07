from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_output(repo_path: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "git command failed").strip())
    return completed.stdout.strip()


def infer_repo_ref(repo_path: str) -> str:
    remote_url = git_output(repo_path, "remote", "get-url", "origin").removesuffix(".git")
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if remote_url.startswith(prefix):
            return remote_url[len(prefix) :]
    return remote_url


def infer_default_branch(repo_path: str) -> str:
    try:
        ref = git_output(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD")
        if ref:
            return ref.removeprefix("refs/remotes/origin/")
    except RuntimeError:
        pass
    try:
        return git_output(repo_path, "branch", "--show-current") or "main"
    except RuntimeError:
        return "main"


def find_repo_root(start_path: str | None = None) -> str | None:
    try:
        return git_output(start_path or os.getcwd(), "rev-parse", "--show-toplevel")
    except RuntimeError:
        return None


def resolve_repo_path(repo_path: str | None = None) -> str | None:
    if repo_path:
        return str(Path(repo_path).resolve())
    return find_repo_root()


def git_diff(repo_path: str, base: str | None = None) -> str:
    try:
        if base:
            return git_output(repo_path, "diff", "--name-only", base)
        return git_output(repo_path, "diff", "--name-only", "HEAD")
    except RuntimeError:
        return ""


def git_changed_files(repo_path: str) -> list[str]:
    diff = git_diff(repo_path)
    return [line for line in diff.splitlines() if line]
