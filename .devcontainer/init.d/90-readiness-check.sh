#!/usr/bin/env bash
# Final honest readiness summary. Deliberately NON-FATAL — this script
# reports, it does not gate. A missing optional tool here must never abort
# bootstrap.sh and take the rest of container start down with it.
set -euo pipefail

pass() { printf 'PASS  %-10s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-10s %s\n' "$1" "$2"; }

echo "==> readiness summary"

if v="$(uv --version 2>/dev/null)"; then pass uv "$v"; else fail uv "not found"; fi
if v="$(python3 --version 2>/dev/null)"; then pass python "$v"; else fail python "not found"; fi
if v="$(node --version 2>/dev/null)"; then pass node "$v"; else fail node "not found"; fi
if command -v tmux >/dev/null 2>&1; then pass tmux "$(tmux -V)"; else fail tmux "not found (Grove hard-requires this)"; fi
if command -v git >/dev/null 2>&1; then pass git "$(git --version)"; else fail git "not found (Grove hard-requires this)"; fi

if command -v docker >/dev/null 2>&1; then
	if docker info >/dev/null 2>&1; then
		pass docker "nested daemon reachable"
	else
		fail docker "CLI present but daemon unreachable"
	fi
else
	fail docker "not found"
fi

# Playwright is deliberately NOT a bare `playwright` on PATH (see
# init.d/46-playwright-browsers.sh) — it resolves through webapp/'s local
# node_modules via `npx`, the same way the test suite invokes it, so this
# check must resolve it the identical way or it would pass while `npx
# playwright test` fails.
webapp_dir="$REPO_ROOT/webapp"
if [ -d "$webapp_dir/node_modules" ]; then
	if v="$(cd "$webapp_dir" && npx --no-install playwright --version 2>/dev/null)"; then
		pass playwright "$v (via npx in webapp/)"
	else
		fail playwright "not resolvable via npx in webapp/"
	fi
	if chromium_bin="$(cd "$webapp_dir" && node -e 'console.log(require("playwright-core").chromium.executablePath())' 2>/dev/null)" \
		&& [ -x "$chromium_bin" ]; then
		pass chromium "$chromium_bin"
	else
		fail chromium "executable not found (Playwright version drift? see Dockerfile PLAYWRIGHT_VERSION)"
	fi
else
	fail playwright "webapp/node_modules missing (npm ci not run)"
fi

echo "==> readiness summary complete (non-fatal)"
exit 0
