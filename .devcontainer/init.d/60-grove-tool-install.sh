#!/usr/bin/env bash
# Editable-installs the `grove` CLI itself so it actually works inside the
# container, not just imports from the checkout. Uses `.[all]` — NOT
# `.[daemon]` — because `[all]` is what makes `grove-mcp` importable; the
# leaner daemon-only extra intentionally omits the MCP SDK and leaves
# `grove-mcp` broken with `ModuleNotFoundError: No module named 'mcp'` (see
# reinstall.sh and the root CLAUDE.md's reinstall lesson).
#
# NEVER run this as root: a root-run entrypoint writes root-owned bytecode
# into this same tool venv, and a later non-root `uv tool install --reinstall`
# cannot unlink it and dies with a bare "Permission denied" naming an
# unrelated dependency. The devcontainer's remoteUser must stay non-root.
set -euo pipefail
[ "${GROVE_INIT_PHASE}" = "create" ] || exit 0

if ! command -v uv >/dev/null 2>&1; then
	echo "-- skipped: uv not on PATH"
	exit 0
fi

cd "$REPO_ROOT"
echo "-- uv tool install --reinstall --force --editable '.[all]'"
uv tool install --reinstall --force --editable '.[all]'
