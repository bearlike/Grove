# Grove installer for Windows.
#
# Usage:
#   iwr -useb https://raw.githubusercontent.com/bearlike/Grove/current/install.ps1 | iex
#   $env:GROVE_SOURCE='<spec>'; iwr -useb https://.../install.ps1 | iex
#
# What it does:
#   1. Installs `uv` if it's not already on PATH (via Astral's official installer).
#   2. Installs Grove as a uv tool, straight from the repo.
#   3. Verifies the install.
#
# Grove is NOT published on PyPI. The name `grove` there belongs to an
# unrelated log-collection framework, so a bare `uv tool install grove`
# installs the wrong product. This script always installs from the repo;
# set GROVE_SOURCE to an explicit spec to point elsewhere.
#
# Env knobs: GROVE_REPO (owner/name), GROVE_REF (branch/tag),
#            GROVE_EXTRAS (daemon|mcp|all|none), GROVE_SOURCE (full spec)
#
# Note: Grove requires tmux at runtime, which on Windows is only available
# under WSL2. On Windows-native, `grove` will install but error at the first
# tmux operation with a clear message pointing you at WSL.

$ErrorActionPreference = 'Stop'

$Repo = if ($env:GROVE_REPO) { $env:GROVE_REPO } else { 'bearlike/Grove' }
$Ref = if ($env:GROVE_REF) { $env:GROVE_REF } else { 'current' }
$Extras = if ($env:GROVE_EXTRAS) { $env:GROVE_EXTRAS } else { 'daemon' }

if ($env:GROVE_SOURCE) {
    $Source = $env:GROVE_SOURCE
} elseif ($Extras -eq 'none') {
    $Source = "grove @ git+https://github.com/$Repo@$Ref"
} else {
    $Source = "grove[$Extras] @ git+https://github.com/$Repo@$Ref"
}

# Install uv if missing.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found — installing via Astral's installer..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv installer ran but the binary is not on PATH. Open a new shell and re-run."
    exit 1
}

Write-Host "installing grove from: $Source"
uv tool install --force $Source

Write-Host ""
grove version
if ($LASTEXITCODE -ne 0) {
    Write-Error "install verification failed: 'grove version' did not run. If 'grove' is not found, open a new shell and re-run."
    exit 1
}

Write-Host ""
Write-Host "grove keeps its files here (created on first use):"
grove debug
Write-Host ""
Write-Host "next:  cd <your git repo> ; grove"
