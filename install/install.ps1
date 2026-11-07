# dops installer for Windows
# Usage: irm https://get.aidecisionops.com/dops.ps1 | iex

$ErrorActionPreference = "Stop"

$InstallDir = if ($env:DOPS_INSTALL_DIR) { $env:DOPS_INSTALL_DIR } else { "$env:USERPROFILE\.dops\bin" }
$Repo = "aidecisionops/cli"
$Version = if ($env:DOPS_VERSION) { $env:DOPS_VERSION } else { "latest" }
$Binary = "dops-windows-x64.exe"

if ($Version -eq "latest") {
  $DownloadUrl = "https://github.com/$Repo/releases/latest/download/$Binary"
} else {
  $DownloadUrl = "https://github.com/$Repo/releases/download/$Version/$Binary"
}

Write-Host "Installing dops for Windows..."
Write-Host "Downloading $Binary from $DownloadUrl..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri $DownloadUrl -OutFile "$InstallDir\dops.exe" -UseBasicParsing

# Add to PATH if needed
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$InstallDir*") {
  [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$currentPath", "User")
  Write-Host "Added $InstallDir to user PATH."
}

Write-Host "dops installed to $InstallDir\dops.exe"
Write-Host "Congrats on your decision to install the dops CLI!"
Write-Host "Run 'dops --help' to get started (restart terminal for PATH changes)."
