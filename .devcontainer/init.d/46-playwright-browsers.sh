#!/usr/bin/env bash
# Chromium only — webapp/playwright.config.ts defines just `mobile-chrome` +
# `desktop-chrome` — pinned implicitly by @playwright/test in
# webapp/package.json, since `npx playwright` always resolves the locally
# installed version rather than whatever the container happens to have on PATH.
#
# The image bakes a chromium build (Tier 0) for the common case, but that build
# is pinned to an ARG while package-lock.json floats under a `^` range — so the
# two CAN drift. Playwright resolves its browser directory from the EXACT
# installed playwright-core version, so a drifted runner fails with "Executable
# doesn't exist" against a directory that looks perfectly populated.
#
# Therefore: do NOT short-circuit on "some chromium-* directory exists" — that
# is precisely the check that makes the drift silent. `playwright install` is
# itself idempotent and exits in well under a second when the exact build is
# already present, so we always let it decide. On a real miss it re-downloads,
# which is why cdn.playwright.dev is on the egress allowlist in .grove/config.json.
set -euo pipefail
[ "${GROVE_INIT_PHASE}" = "create" ] || exit 0

webapp_dir="$REPO_ROOT/webapp"
if [ ! -d "$webapp_dir" ]; then
	echo "-- skipped: no webapp/ directory"
	exit 0
fi

cd "$webapp_dir"
if [ ! -d node_modules ]; then
	echo "-- skipped: node_modules missing, webapp deps step must run first"
	exit 0
fi

echo "-- npx playwright install chromium"
npx playwright install chromium
