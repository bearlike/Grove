#!/usr/bin/env bash
# Webapp Node deps. Matches the Makefile's `webapp-install` target (`npm ci`
# in webapp/) rather than inventing a separate command, so container and host
# installs stay identical. Heavy — create only.
set -euo pipefail
[ "${GROVE_INIT_PHASE}" = "create" ] || exit 0

webapp_dir="$REPO_ROOT/webapp"
if [ ! -d "$webapp_dir" ]; then
	echo "-- skipped: no webapp/ directory"
	exit 0
fi

cd "$webapp_dir"
echo "-- npm ci"
npm ci
