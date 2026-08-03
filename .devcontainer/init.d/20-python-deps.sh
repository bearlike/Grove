#!/usr/bin/env bash
# Python deps for the whole workspace. Mirrors CI's install step exactly
# (.github/workflows/ci.yml "Install": `uv sync --all-groups`) so a container
# session runs against the same dependency set CI gates on. Heavy — create only.
set -euo pipefail
[ "${GROVE_INIT_PHASE}" = "create" ] || exit 0

cd "$REPO_ROOT"
echo "-- uv sync --all-groups"
uv sync --all-groups
