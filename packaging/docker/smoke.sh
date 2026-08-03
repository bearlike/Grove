#!/usr/bin/env bash
# Grove clean-install smoke test. Runs INSIDE the packaging/docker image (a
# fresh Ubuntu with uv but no Grove): installs Grove exactly like a new user,
# then drives the whole first-run path with zero human input.
#
#   make install-smoke      # build the image + run this script on the checkout
#   GROVE_INSTALL_SPEC='grove[all] @ git+https://github.com/bearlike/Grove@current' \
#     make install-smoke    # release mode: test the public install path instead
#
# Asserted, in order:
#   1. uv tool install + `grove version` (proves the entry point is OUR grove,
#      not the unrelated PyPI name-squat)
#   2. the other two console scripts are live: `grove-agent-hook` and
#      `grove-mcp`, the latter down to its FastMCP wiring
#   3. graceful typed error outside a git repo (no traceback)
#   4. `grove debug` path map + `grove config init` scaffold
#   5. zero-config workspace create → worktree + tmux session + state.json,
#      including the first-run `initialized <dir>` log lines (GROVE_DEBUG=1)
#   6. daemon: healthz, 401 unauthenticated, pair → approve → token → authed list
#   7. kill → worktree, tmux session, and listing all cleaned up
#
# The spec installs `[all]`, not the lean `[daemon]`: the shipped console
# scripts are only install-tested if their extras are actually resolved.
set -euo pipefail

SPEC="${GROVE_INSTALL_SPEC:-grove[all] @ file:///src}"
BOLD=$'\033[1m'
RESET=$'\033[0m'

step() { printf '\n%s── %s%s\n' "$BOLD" "$*" "$RESET"; }
pass() { printf '  ok: %s\n' "$*"; }
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

# The whole point is a clean first run — refuse a dirty home.
[ ! -e "$HOME/.config/grove" ] || fail "pre-existing ~/.config/grove; not a clean home"
[ ! -e "$HOME/.local/state/grove" ] || fail "pre-existing ~/.local/state/grove; not a clean home"

# Surface the `initialized <dir>` INFO lines everywhere.
export GROVE_DEBUG=1
# The built-in `shell` agent launches "$SHELL"; pin it for the tmux pane.
export SHELL=/bin/bash

step "install: uv tool install ${SPEC}"
uv tool install --force "${SPEC}"
command -v grove >/dev/null || fail "grove not on PATH after install"
pass "installed at $(command -v grove)"

step "verify: grove version"
version_out="$(grove version)"
case "$version_out" in
grove\ *) pass "$version_out" ;;
*) fail "unexpected 'grove version' output: ${version_out} (wrong 'grove' package?)" ;;
esac

step "verify: grove-agent-hook + grove-mcp entry points"
# The hook hand-parses argv and reads stdin (it has no --help), so an empty
# payload is its cheapest liveness probe: it must import and no-op, exit 0.
grove-agent-hook </dev/null || fail "grove-agent-hook failed on an empty payload"
grove-mcp --help >/dev/null || fail "grove-mcp --help failed"
# --help returns BEFORE the SDK is touched (the FastMCP import is deferred so a
# [daemon]-only host gets an install hint instead of a traceback), so construct
# the server too — that is the line a new major of the MCP SDK breaks.
"$(uv tool dir)/grove/bin/python" - <<'PY' || fail "grove-mcp cannot build its FastMCP server (incompatible mcp SDK?)"
from grove.mcp.server import GroveMcpServer, McpServerConfig

GroveMcpServer(McpServerConfig.from_env({}))
PY
pass "all three console scripts live, FastMCP wiring included"

step "verify: graceful error outside a git repo"
cd "$HOME"
set +e
ls_err="$(grove ls 2>&1)"
ls_rc=$?
set -e
[ "$ls_rc" -eq 1 ] || fail "grove ls outside a repo: expected exit 1, got ${ls_rc}"
grep -qi "git repository" <<<"$ls_err" || fail "missing typed error message, got: ${ls_err}"
if grep -q "Traceback (most recent call last)" <<<"$ls_err"; then
    fail "traceback leaked to the user: ${ls_err}"
fi
pass "typed one-line error, exit 1"

step "verify: grove debug path map"
dbg="$(grove debug)"
jq -e '.user_config_path and .user_state_path and .user_schema_path' <<<"$dbg" >/dev/null \
    || fail "grove debug is missing path keys: ${dbg}"
state_file="$(jq -r .user_state_path <<<"$dbg")"
schema_file="$(jq -r .user_schema_path <<<"$dbg")"
pass "config/state paths resolved under \$HOME"

step "scaffold: grove config init in a fresh repo"
repo="$HOME/demo"
git init -q -b main "$repo"
cd "$repo"
git config user.email smoke@example.invalid
git config user.name "Grove Smoke"
echo "hello grove" >README.md
git add README.md
git commit -qm "init"
grove config init
[ -f .grove/config.json ] || fail ".grove/config.json not scaffolded"
[ -f "$schema_file" ] || fail "user schema not written at ${schema_file}"
grove config show >/dev/null || fail "grove config show failed on the scaffold"
pass "project config + user schema written"

step "create: zero-config workspace with the built-in shell agent"
create_err="$HOME/create.stderr"
grove create "smoke workspace" --agent shell 2>"$create_err" | tee "$HOME/create.out"
wsid="$(sed -n 's/^created //p' "$HOME/create.out")"
[ -n "$wsid" ] || {
    cat "$create_err" >&2
    fail "no workspace id in create output"
}
[ -f "$state_file" ] || fail "state.json not created at ${state_file}"
grep -q "initialized" "$create_err" || fail "no 'initialized <dir>' first-run lines on stderr"
session="$(grove ls | jq -r '.[0].tmux_session')"
worktree="$(grove ls | jq -r '.[0].worktree_path')"
grove ls | jq -e 'length == 1' >/dev/null || fail "grove ls should list exactly 1 workspace"
tmux has-session -t "$session" 2>/dev/null || fail "tmux session ${session} missing"
[ -d "$worktree" ] || fail "worktree ${worktree} missing"
pass "workspace ${wsid} → ${session} @ ${worktree}"

step "daemon: serve → pair → approve → authed list"
grove daemon serve --port 0 --print-port >"$HOME/daemon.port" 2>"$HOME/daemon.log" &
daemon_pid=$!
for _ in $(seq 1 50); do
    [ -s "$HOME/daemon.port" ] && break
    sleep 0.2
done
[ -s "$HOME/daemon.port" ] || {
    cat "$HOME/daemon.log" >&2
    fail "daemon never printed its port"
}
port="$(head -n1 "$HOME/daemon.port" | tr -d '[:space:]')"
base="http://127.0.0.1:${port}"
curl -fsS "$base/healthz" >/dev/null || fail "healthz failed"
unauth_code="$(curl -s -o /dev/null -w '%{http_code}' "$base/workspaces")"
[ "$unauth_code" = "401" ] || fail "unauthenticated /workspaces: expected 401, got ${unauth_code}"
challenge_id="$(curl -fsS -X POST -H 'Content-Type: application/json' \
    -d '{"label":"smoke-client"}' "$base/auth/pair" | jq -r .challenge_id)"
[ -n "$challenge_id" ] && [ "$challenge_id" != null ] || fail "pair init returned no challenge_id"
grove auth approve "$challenge_id" >/dev/null
token="$(curl -fsS "$base/auth/pair/${challenge_id}" | jq -r .token)"
[ -n "$token" ] && [ "$token" != null ] || fail "no token after approve"
curl -fsS -H "Authorization: Bearer ${token}" "$base/workspaces" \
    | jq -e 'length >= 1' >/dev/null || fail "authenticated /workspaces did not list the workspace"
kill "$daemon_pid"
wait "$daemon_pid" 2>/dev/null || true
pass "pairing handshake + authenticated list on :${port}"

step "kill: full cleanup"
grove kill "$wsid" --yes
[ ! -d "$worktree" ] || fail "worktree still present after kill"
if tmux has-session -t "$session" 2>/dev/null; then
    fail "tmux session survived kill"
fi
grove ls | jq -e 'length == 0' >/dev/null || fail "workspace still listed after kill"
pass "worktree, tmux session, and listing all clean"

step "bootstrap inventory (files Grove initialized this run)"
find "$HOME/.config/grove" "$HOME/.local/state/grove" -maxdepth 2 | sed "s|^$HOME/|  ~/|"

printf '\n%sPASS%s: clean install + first run verified with zero human input.\n' "$BOLD" "$RESET"
