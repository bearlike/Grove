#!/usr/bin/env bash
# Seeds webapp/.env.local from webapp/.env.example if absent, matching the
# existing `.grove/config.json` init_script so container and host worktrees
# agree on how this file gets created. No phase guard: a cheap existence
# check that must re-apply on every start per the devcontainer contract.
set -euo pipefail

webapp_dir="$REPO_ROOT/webapp"
if [ ! -d "$webapp_dir" ]; then
	echo "-- skipped: no webapp/ directory"
	exit 0
fi

if [ -f "$webapp_dir/.env.local" ]; then
	echo "-- skipped: webapp/.env.local already exists"
	exit 0
fi

if [ ! -f "$webapp_dir/.env.example" ]; then
	echo "-- skipped: webapp/.env.example missing"
	exit 0
fi

cp "$webapp_dir/.env.example" "$webapp_dir/.env.local"
echo "-- ok: webapp/.env.local created from .env.example"
