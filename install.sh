#!/usr/bin/env bash
# Grove installer for Linux + macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/main/install.sh | bash -s -- --canary
#
# What it does:
#   1. Installs `uv` if it's not already on PATH (via Astral's official script).
#   2. Installs Grove as a uv tool: `uv tool install grove` (or git+main for canary).
#
# After install, run:  grove --help

set -euo pipefail

REPO="${GROVE_REPO:-bearlike/Grove}"
SOURCE="grove"

for arg in "${@:-}"; do
  case "$arg" in
    --canary)  SOURCE="git+https://github.com/${REPO}@main" ;;
    --stable)  SOURCE="grove" ;;
    --git=*)   SOURCE="${arg#--git=}" ;;
    "" )       ;;
    *)
      echo "unknown flag: $arg" >&2
      echo "usage: install.sh [--canary | --stable | --git=<spec>]" >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found — installing via Astral's installer..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv places its binary under ~/.local/bin (or similar); make it visible now.
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv installer ran but the binary is not on PATH." >&2
  echo "Open a new shell or add ~/.local/bin to PATH and re-run." >&2
  exit 1
fi

echo "installing grove from: ${SOURCE}"
uv tool install --force "${SOURCE}"

echo
echo "installed."
grove version 2>/dev/null || true
echo
echo "next:  grove --help"
