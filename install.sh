#!/usr/bin/env bash
# Grove installer for Linux + macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/current/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/current/install.sh | bash -s -- --git=<spec>
#
# What it does:
#   1. Installs `uv` if it's not already on PATH (via Astral's official script).
#   2. Installs Grove as a uv tool, straight from the repo.
#   3. Verifies the install and prints where Grove keeps its files.
#
# Grove is NOT published on PyPI. The name `grove` there belongs to an
# unrelated log-collection framework, so a bare `uv tool install grove`
# installs the wrong product (issue #105). This script always installs
# from the repo; use --git=<spec> or the env knobs to point elsewhere.
#
# Env knobs: GROVE_REPO (owner/name), GROVE_REF (branch/tag),
#            GROVE_EXTRAS (daemon|mcp|all|none)

set -euo pipefail

REPO="${GROVE_REPO:-bearlike/Grove}"
REF="${GROVE_REF:-current}"        # the public mirror's snapshot branch
EXTRAS="${GROVE_EXTRAS:-daemon}"   # daemon = TUI + web-dashboard backend

if [ "${EXTRAS}" = "none" ]; then
  SOURCE="grove @ git+https://github.com/${REPO}@${REF}"
else
  SOURCE="grove[${EXTRAS}] @ git+https://github.com/${REPO}@${REF}"
fi

for arg in "${@:-}"; do
  case "$arg" in
    --git=*)   SOURCE="${arg#--git=}" ;;
    "" )       ;;
    *)
      echo "unknown flag: $arg" >&2
      echo "usage: install.sh [--git=<spec>]   (env: GROVE_REPO, GROVE_REF, GROVE_EXTRAS)" >&2
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
if ! grove version; then
  echo "install verification failed: 'grove version' did not run." >&2
  echo "If 'grove' is not found, add ~/.local/bin to PATH and re-run this script." >&2
  exit 1
fi

echo
echo "grove keeps its files here (created on first use):"
grove debug
echo
echo "next:  cd <your git repo> && grove"
