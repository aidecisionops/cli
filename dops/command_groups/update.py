from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .. import __version__
from ..argparse_utils import DopsHelpFormatter, add_examples
from ..http import urlopen_with_retries
from ..installers.templates import POWERSHELL_INSTALLER_URL, SHELL_INSTALLER_URL
from ..tls import create_ssl_context


def _installed_binary_path(install_dir: str | None) -> Path:
    target_dir = Path(install_dir).expanduser() if install_dir else Path.home() / ".dops" / "bin"
    binary_name = "dops.exe" if platform.system().lower().startswith("win") else "dops"
    return target_dir / binary_name


def _release_artifact_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system.startswith("darwin"):
        os_name = "darwin"
    elif system.startswith("linux"):
        os_name = "linux"
    elif system.startswith("win"):
        os_name = "windows"
    else:
        os_name = system

    if machine in {"x86_64", "amd64"}:
        arch = "x64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = machine

    name = f"dops-{os_name}-{arch}"
    return f"{name}.exe" if os_name == "windows" else name


def _resolve_target_release(version: str) -> str | None:
    artifact = _release_artifact_name()
    if version != "latest":
        return version
    url = f"https://github.com/aidecisionops/cli/releases/latest/download/{artifact}"
    request = urllib.request.Request(url, method="HEAD")
    try:
        final_url = urlopen_with_retries(request, timeout=10, context=create_ssl_context()).url
    except (RuntimeError, urllib.error.URLError):
        return None
    match = re.search(r"/releases/download/([^/]+)/", final_url)
    return match.group(1) if match else None


def run_update(flags: argparse.Namespace) -> None:
    from ..ui import console

    target_version = flags.version or "latest"
    current_version = __version__
    resolved_target = _resolve_target_release(target_version) or target_version
    console.print(f"Updating dops from {current_version} to {resolved_target}...")
    env = os.environ.copy()
    if flags.version:
        env["DOPS_VERSION"] = flags.version
    if flags.install_dir:
        env["DOPS_INSTALL_DIR"] = flags.install_dir
    if platform.system().lower().startswith("win"):
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", f"irm {POWERSHELL_INSTALLER_URL} | iex"]
    else:
        command = ["sh", "-c", f"curl -fsSL {shlex.quote(SHELL_INSTALLER_URL)} | sh"]
    completed = subprocess.run(command, env=env, check=False, timeout=600)
    if completed.returncode != 0:
        raise RuntimeError(f"Update failed with exit code {completed.returncode}.")

    installed_binary = _installed_binary_path(flags.install_dir)
    if installed_binary.exists():
        try:
            version_completed = subprocess.run(
                [str(installed_binary), "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            version_completed = None
        if version_completed and version_completed.returncode == 0:
            console.print(f"Installed binary: {installed_binary} ({version_completed.stdout.strip()})")
        else:
            fallback_label = f" ({resolved_target})" if resolved_target else ""
            console.print(f"Installed binary: {installed_binary}{fallback_label}")
    resolved_binary = shutil.which("dops")
    if resolved_binary and Path(resolved_binary).resolve() != installed_binary.resolve():
        console.print(
            "[yellow]Current shell still resolves `dops` to "
            f"{resolved_binary}. Start a new shell or run the installed binary directly.[/yellow]"
        )


def register_update_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    update = subparsers.add_parser(
        "update",
        formatter_class=DopsHelpFormatter,
        help="Update the dops CLI to the latest released binary",
        description="Update the dops CLI to the latest released binary",
        aliases=["self-update"],
    )
    update.add_argument("--version", help="Specific release tag to install (defaults to latest)")
    update.add_argument("--install-dir", help="Target directory for the dops binary (default: ~/.dops/bin)")
    update.set_defaults(func=run_update)
    add_examples(update, ["dops update", "dops update --version v0.1.28"])
