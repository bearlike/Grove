# Grove installer for Windows.
#
# Usage:
#   iwr -useb https://raw.githubusercontent.com/bearlike/Grove/main/install.ps1 | iex
#   $env:GROVE_CHANNEL='canary'; iwr -useb https://.../install.ps1 | iex
#
# What it does:
#   1. Installs `uv` if it's not already on PATH (via Astral's official installer).
#   2. Installs Grove as a uv tool.
#
# Note: Grove requires tmux at runtime, which on Windows is only available
# under WSL2. On Windows-native, `grove` will install but error at the first
# tmux operation with a clear message pointing you at WSL.

$ErrorActionPreference = 'Stop'

$Repo = if ($env:GROVE_REPO) { $env:GROVE_REPO } else { 'bearlike/Grove' }
$Channel = if ($env:GROVE_CHANNEL) { $env:GROVE_CHANNEL } else { 'stable' }

switch ($Channel) {
    'stable' { $Source = 'grove' }
    'canary' { $Source = "git+https://github.com/$Repo@main" }
    default  { $Source = $Channel }  # pass-through for explicit specs
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
Write-Host "installed."
try { grove version } catch { }
Write-Host ""
Write-Host "next:  grove --help"
