from __future__ import annotations

DEFAULT_INSTALL_BASE_URL = "https://get.aidecisionops.com"
DEFAULT_RELEASE_REPO_SLUG = "aidecisionops/cli"
SHELL_INSTALL_PATH = "/dops"
POWERSHELL_INSTALL_PATH = "/dops.ps1"
SHELL_INSTALLER_URL = f"{DEFAULT_INSTALL_BASE_URL}{SHELL_INSTALL_PATH}"
POWERSHELL_INSTALLER_URL = f"{DEFAULT_INSTALL_BASE_URL}{POWERSHELL_INSTALL_PATH}"


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def render_shell_installer(install_base_url: str = DEFAULT_INSTALL_BASE_URL, release_repo_slug: str = DEFAULT_RELEASE_REPO_SLUG) -> str:
    install_base_url = _normalize_base_url(install_base_url)
    return f"""#!/bin/sh
set -e

# dops installer for macOS/Linux
# Usage: curl -fsSL {install_base_url}{SHELL_INSTALL_PATH} | sh

INSTALL_DIR="${{DOPS_INSTALL_DIR:-$HOME/.dops/bin}}"
REPO="{release_repo_slug}"
VERSION="${{DOPS_VERSION:-latest}}"
INSTALL_PATH="${{INSTALL_DIR}}/dops"

append_path_entry() {{
  RC="$1"
  LINE="export PATH=\\"${{INSTALL_DIR}}:\\$PATH\\""
  if [ -f "$RC" ] && grep -Fqs "$LINE" "$RC"; then
    return
  fi
  echo "" >> "$RC"
  echo "# dops" >> "$RC"
  echo "$LINE" >> "$RC"
  echo "Added ${{INSTALL_DIR}} to PATH in ${{RC}}"
}}

print_post_install_notes() {{
  RELEASE_VERSION="$1"
  INSTALLED_VERSION="$("$INSTALL_PATH" --version 2>/dev/null || true)"
  if [ -z "$INSTALLED_VERSION" ]; then
    INSTALLED_VERSION="${{RELEASE_VERSION:-unknown}}"
  fi
  CURRENT_DOPS="$(command -v dops 2>/dev/null || true)"

  echo "Installed version: ${{INSTALLED_VERSION}}"
  if [ -n "$CURRENT_DOPS" ] && [ "$CURRENT_DOPS" != "$INSTALL_PATH" ]; then
    echo ""
    echo "Note: your current shell resolves dops to ${{CURRENT_DOPS}}"
    echo "The newly installed binary is at ${{INSTALL_PATH}}"
    echo "Run '${{INSTALL_PATH}} --version' now, or start a new shell to pick up the updated PATH."
  fi
}}

detect_platform() {{
  OS="$(uname -s)"
  ARCH="$(uname -m)"
  case "$OS" in
    Darwin) OS="darwin" ;;
    Linux) OS="linux" ;;
    *) echo "Unsupported OS: $OS" >&2; exit 1 ;;
  esac
  case "$ARCH" in
    x86_64|amd64) ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
  esac
  echo "${{OS}}-${{ARCH}}"
}}

resolve_version() {{
  if [ "$VERSION" != "latest" ]; then
    echo "$VERSION"
    return
  fi
  LOCATION="$(curl -fsS -o /dev/null -w '%{{redirect_url}}' "https://github.com/${{REPO}}/releases/latest/download/${{BINARY}}")"
  if [ -n "$LOCATION" ]; then
    printf '%s' "$LOCATION" | sed -n 's#^.*/download/\\([^/]*\\)/.*#\\1#p'
  fi
}}

main() {{
  PLATFORM=$(detect_platform)
  BINARY="dops-${{PLATFORM}}"

  RESOLVED_VERSION="$(resolve_version)"
  if [ -n "$RESOLVED_VERSION" ] && [ "$RESOLVED_VERSION" != "latest" ]; then
    DOWNLOAD_URL="https://github.com/${{REPO}}/releases/download/${{RESOLVED_VERSION}}/${{BINARY}}"
  else
    DOWNLOAD_URL="https://github.com/${{REPO}}/releases/latest/download/${{BINARY}}"
  fi

  echo "Installing dops for ${{PLATFORM}}..."
  echo "Downloading ${{BINARY}} from ${{DOWNLOAD_URL}}..."
  mkdir -p "$INSTALL_DIR"
  TMP_PATH="$(mktemp "${{INSTALL_DIR}}/.dops.XXXXXX")"
  trap 'rm -f "$TMP_PATH"' EXIT INT TERM
  curl -fSL --progress-bar "$DOWNLOAD_URL" -o "$TMP_PATH"
  chmod +x "$TMP_PATH"

  # Verify the binary works before installing
  if ! "$TMP_PATH" --version >/dev/null 2>&1; then
    echo "Error: downloaded binary failed verification. The download may be corrupt." >&2
    echo "Please try again. If the problem persists, report it at https://github.com/${{REPO}}/issues" >&2
    rm -f "$TMP_PATH"
    exit 1
  fi

  mv "$TMP_PATH" "$INSTALL_PATH"
  trap - EXIT INT TERM

  # Add to PATH if needed
  case ":$PATH:" in
    *":${{INSTALL_DIR}}:"*) ;;
    *)
      SHELL_NAME="$(basename "$SHELL")"
      case "$SHELL_NAME" in
        zsh) RC="$HOME/.zshrc" ;;
        bash) RC="$HOME/.bashrc" ;;
        fish) RC="$HOME/.config/fish/config.fish" ;;
        *) RC="" ;;
      esac
      if [ -n "$RC" ]; then
        append_path_entry "$RC"
      else
        echo "Add ${{INSTALL_DIR}} to your PATH manually."
      fi
      ;;
  esac

  echo "dops installed to ${{INSTALL_PATH}}"
  print_post_install_notes "$RESOLVED_VERSION"
  echo "Congrats on your decision to install the dops CLI!"
  echo "Run 'dops --help' to get started."
}}

main
"""


def render_powershell_installer(
    install_base_url: str = DEFAULT_INSTALL_BASE_URL,
    release_repo_slug: str = DEFAULT_RELEASE_REPO_SLUG,
) -> str:
    install_base_url = _normalize_base_url(install_base_url)
    return f"""# dops installer for Windows
# Usage: irm {install_base_url}{POWERSHELL_INSTALL_PATH} | iex

$ErrorActionPreference = "Stop"

$InstallDir = if ($env:DOPS_INSTALL_DIR) {{ $env:DOPS_INSTALL_DIR }} else {{ "$env:USERPROFILE\\.dops\\bin" }}
$Repo = "{release_repo_slug}"
$Version = if ($env:DOPS_VERSION) {{ $env:DOPS_VERSION }} else {{ "latest" }}
$Binary = "dops-windows-x64.exe"

if ($Version -eq "latest") {{
  $DownloadUrl = "https://github.com/$Repo/releases/latest/download/$Binary"
}} else {{
  $DownloadUrl = "https://github.com/$Repo/releases/download/$Version/$Binary"
}}

Write-Host "Installing dops for Windows..."
Write-Host "Downloading $Binary from $DownloadUrl..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri $DownloadUrl -OutFile "$InstallDir\\dops.exe" -UseBasicParsing

# Add to PATH if needed
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$InstallDir*") {{
  [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$currentPath", "User")
  Write-Host "Added $InstallDir to user PATH."
}}

Write-Host "dops installed to $InstallDir\\dops.exe"
Write-Host "Congrats on your decision to install the dops CLI!"
Write-Host "Run 'dops --help' to get started (restart terminal for PATH changes)."
"""
