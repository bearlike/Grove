#!/usr/bin/env bash
# Grove dev reinstaller: refresh every running surface from this checkout.
#
# For contributors running Grove from an editable `uv tool` install. End users
# upgrade with `uv tool upgrade grove-factory` instead. New code on disk changes
# nothing already running, so this script reinstalls, rebuilds, restarts, then
# proves each surface serves the new code.
#
# It uses no Node from your machine. Node and npm ship inside Grove's own tool
# venv (the `nodejs-wheel` dependency), and the dashboard build runs on those.
#
# Safe to run unattended: it never prompts, reruns cleanly, and builds the new
# dashboard BEFORE touching a live service, so a failed build leaves everything
# serving the previous version. Any failure exits non-zero and names the step.
#
# Usage:
#   ./reinstall.sh                 reinstall + rebuild + restart + verify
#   ./reinstall.sh --no-reinstall  keep the current tool venv (editable code is live anyway)
#   ./reinstall.sh --no-webapp     skip the dashboard rebuild and restart
#   ./reinstall.sh --no-daemon     skip the daemon restart
#   ./reinstall.sh --no-mcp        skip restarting a networked grove-mcp service
#   ./reinstall.sh --no-verify     skip the verification pass
#   ./reinstall.sh -h | --help
#
# Service names, ports and the daemon URL are read from this host, never assumed.

set -euo pipefail

# ─── options ──────────────────────────────────────────────────────────────────
DO_REINSTALL=1 DO_WEBAPP=1 DO_DAEMON=1 DO_MCP=1 DO_VERIFY=1

usage() {  # print the header comment above, which is the one source of truth
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
  exit "${1:-0}"
}

for arg in "$@"; do
  case "$arg" in
    --no-reinstall) DO_REINSTALL=0 ;;
    --no-webapp)    DO_WEBAPP=0 ;;
    --no-daemon)    DO_DAEMON=0 ;;
    --no-mcp)       DO_MCP=0 ;;
    --no-verify)    DO_VERIFY=0 ;;
    -h|--help)      usage 0 ;;
    *) printf 'unknown option: %s\n\n' "$arg" >&2; usage 2 ;;
  esac
done

# ─── output ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\033[1m' DIM=$'\033[2m' RED=$'\033[31m' GREEN=$'\033[32m'
  YELLOW=$'\033[33m' BLUE=$'\033[34m' OFF=$'\033[0m'
else
  BOLD="" DIM="" RED="" GREEN="" YELLOW="" BLUE="" OFF=""
fi

CURRENT_STEP="startup"
step() { CURRENT_STEP="$1"; printf '\n%s%s▸ %s%s\n' "$BOLD" "$BLUE" "$1" "$OFF"; }
info() { printf '  %s%s%s\n' "$DIM" "$1" "$OFF"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
die()  { printf '  %s✗%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }
run()  { "$@" 2>&1 | sed 's/^/    /'; }  # indent a command's output under its step

trap 'printf "\n%s✗ failed during: %s%s\n  line %s: %s\n" "$RED" "$CURRENT_STEP" "$OFF" "$LINENO" "$BASH_COMMAND" >&2' ERR

# ─── context ──────────────────────────────────────────────────────────────────
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" \
  || die "not inside a git checkout; run this from the Grove repository"
cd "$REPO_ROOT"
command -v uv >/dev/null 2>&1 || die "uv is not on PATH (https://docs.astral.sh/uv/)"

HAVE_SYSTEMD=0
systemctl --user show-environment >/dev/null 2>&1 && HAVE_SYSTEMD=1

unit_named() {  # $1 = daemon|webapp|mcp → the installed grove unit, or nothing
  [ "$HAVE_SYSTEMD" = 1 ] || return 0
  # list-unit-files exits non-zero when nothing matches, which is an answer here.
  systemctl --user list-unit-files "grove-$1.service" --no-legend 2>/dev/null \
    | awk 'NR==1 {print $1}' || true
}
DAEMON_UNIT="$(unit_named daemon)"
WEBAPP_UNIT="$(unit_named webapp)"
MCP_UNIT="$(unit_named mcp)"   # only a networked grove-mcp has one; stdio needs none

restart() {  # $1 = unit (may be empty), $2 = label
  if [ -z "$1" ]; then
    warn "no $2 service installed; restart it however it runs on this host"
    return 0
  fi
  systemctl --user restart "$1"
  ok "$2 restarted ($1)"
}

printf '%s%sGrove reinstall%s  %s%s%s\n' "$BOLD" "$BLUE" "$OFF" "$DIM" "$(git log --oneline -1)" "$OFF"

# ─── 1. package ───────────────────────────────────────────────────────────────
# One editable install carries every surface plus Grove's own Node and npm.
assert_venv_owned_by_me() {
  # A grove entrypoint once run as root (e.g. `sudo claude` spawning grove-mcp)
  # leaves root-owned bytecode that uv cannot delete, and uv's error never says
  # why. Name the cause instead.
  local shim venv intruder
  shim="$(command -v grove 2>/dev/null)" || return 0
  venv="$(dirname "$(dirname "$(readlink -f "$shim")")")"
  intruder="$(find "$venv" ! -user "$(id -un)" -print -quit 2>/dev/null || true)"
  [ -z "$intruder" ] && return 0
  die "$(printf '%s\n      %s\n      %s' \
    "the grove venv holds files you do not own, e.g. ${intruder#"$venv/"}" \
    "cause: a grove command ran as another user (usually root)" \
    "fix:   sudo chown -R $(id -un): $venv   # then rerun")"
}

if [ "$DO_REINSTALL" = 1 ]; then
  step "Reinstall the package"
  assert_venv_owned_by_me
  # Tools from before the distribution was renamed would otherwise linger beside it.
  for old in grove-crew grove; do
    uv tool list 2>/dev/null | grep -q "^$old " && run uv tool uninstall "$old"
  done
  run uv tool install --reinstall --force --editable .
  ok "grove $("$(uv tool dir --bin)/grove" version 2>/dev/null | awk '{print $NF}') installed"
fi

# The grove that uv manages, never merely the first one on PATH: a service unit
# is written against this path, and it must survive the next reinstall.
GROVE_SHIM="$(uv tool dir --bin)/grove"
[ -x "$GROVE_SHIM" ] || die "no uv-managed grove at $GROVE_SHIM; rerun without --no-reinstall"
TOOL_BIN="$(dirname "$(readlink -f "$GROVE_SHIM")")"
[ -x "$TOOL_BIN/npm" ] || die "Grove's bundled npm is missing from $TOOL_BIN; rerun without --no-reinstall"

wait_for_http() {  # $1 = url, $2 = seconds → echoes the last HTTP status
  local code="000" i
  for ((i = 0; i < $2; i++)); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" || true)"
    [ "$code" != 000 ] && break
    sleep 1
  done
  echo "$code"
}

unit_port() {  # $1 = unit → the --port its ExecStart names, if any
  systemctl --user cat "$1" 2>/dev/null | sed -n 's/.*--port[= ]\([0-9]\{1,\}\).*/\1/p' | head -1
}

unit_serves_grove_web() {
  systemctl --user cat "$WEBAPP_UNIT" 2>/dev/null | grep -q '^ExecStart=.* web --host'
}

# A unit written for the old `npm run start` layout serves webapp/.next on the
# host's Node. Move it to `grove web`, keeping its host and port, and put the old
# unit back untouched if the new one does not answer: the dashboard is never left
# down by a migration.
migrate_webapp_unit() {
  local file backup port host code
  file="$(systemctl --user show "$WEBAPP_UNIT" -p FragmentPath --value)"
  backup="$file.pre-grove-web"
  port="$(unit_port "$WEBAPP_UNIT")"
  host="$(systemctl --user cat "$WEBAPP_UNIT" | sed -n 's/.*--hostname \([^ ]*\).*/\1/p' | head -1)"
  info "migrating $WEBAPP_UNIT to 'grove web' on ${host:-0.0.0.0}:${port:-3000} (old unit kept as ${backup##*/})"
  cp -f "$file" "$backup"
  run make _systemd-install-webapp WITH_WEBAPP=1 GROVE_BIN="$GROVE_SHIM" \
    WEBAPP_PORT="${port:-3000}" WEBAPP_HOST="${host:-0.0.0.0}"
  systemctl --user daemon-reload
  systemctl --user restart "$WEBAPP_UNIT"
  code="$(wait_for_http "http://127.0.0.1:${port:-3000}/login" 30)"
  case "$code" in
    200|302|307) ok "dashboard migrated and serving (HTTP $code)" ;;
    *)
      cp -f "$backup" "$file"
      systemctl --user daemon-reload
      systemctl --user restart "$WEBAPP_UNIT"
      die "the migrated dashboard answered HTTP $code, so the previous unit was restored; see 'journalctl --user -u $WEBAPP_UNIT'"
      ;;
  esac
}

# ─── 2. dashboard ─────────────────────────────────────────────────────────────
if [ "$DO_WEBAPP" = 1 ]; then
  step "Rebuild the web dashboard"
  info "node $("$TOOL_BIN/node" -p process.versions.node), npm $("$TOOL_BIN/npm" -v), both from Grove's venv"
  # Builds beside the live bundle and swaps it in only on success, so the running
  # dashboard keeps serving the previous build if anything below fails.
  run make webapp-bundle WEBAPP_NPM_BIN="$TOOL_BIN/npm"
  "$GROVE_SHIM" web --check >/dev/null || die "the new bundle does not resolve; see 'grove web --check'"
  ok "bundle ready in src/grove/_webapp"

  if [ -n "$WEBAPP_UNIT" ] && ! unit_serves_grove_web; then
    migrate_webapp_unit
  else
    restart "$WEBAPP_UNIT" "dashboard"
  fi
fi

# ─── 3. daemon and MCP ────────────────────────────────────────────────────────
if [ "$DO_DAEMON" = 1 ]; then
  step "Restart the daemon"
  restart "$DAEMON_UNIT" "daemon"
fi

# A networked grove-mcp keeps the modules it imported at startup, so it would
# serve the previous build until restarted. A stdio server respawns per client.
if [ "$DO_MCP" = 1 ] && [ -n "$MCP_UNIT" ]; then
  step "Restart the networked MCP server"
  restart "$MCP_UNIT" "mcp"
fi

# ─── 4. verify ────────────────────────────────────────────────────────────────
if [ "$DO_VERIFY" = 1 ]; then
  step "Verify"
  if "$GROVE_SHIM" debug >/dev/null 2>&1; then ok "config loads"; else warn "grove debug failed; check 'grove config show'"; fi
  if "$TOOL_BIN/grove-mcp" --help >/dev/null 2>&1; then ok "grove-mcp runs"; else warn "grove-mcp does not start"; fi

  if [ -n "$DAEMON_UNIT" ]; then
    daemon="http://127.0.0.1:$(unit_port "$DAEMON_UNIT" || true)"
    [ "$daemon" = "http://127.0.0.1:" ] && daemon="http://127.0.0.1:7421"
    code="$(wait_for_http "$daemon/healthz" 20)"
    if [ "$code" = 200 ]; then ok "daemon healthy at $daemon"; else warn "daemon /healthz answered ${code} at $daemon"; fi
  fi

  if [ -n "$WEBAPP_UNIT" ]; then
    web="http://127.0.0.1:$(unit_port "$WEBAPP_UNIT" || true)"
    [ "$web" = "http://127.0.0.1:" ] && web="http://127.0.0.1:3000"
    code="$(wait_for_http "$web/login" 20)"
    case "$code" in
      200|302|307) ok "dashboard serving at $web" ;;
      *)           warn "dashboard answered ${code} at $web; see 'journalctl --user -u $WEBAPP_UNIT'" ;;
    esac
  fi

  if [ -n "$MCP_UNIT" ]; then
    # The MCP endpoint rejects a bare GET, so any HTTP status means it is listening.
    port="$(unit_port "$MCP_UNIT" || true)"
    code=000
    if [ -n "$port" ]; then code="$(wait_for_http "http://127.0.0.1:$port/mcp" 10)"; fi
    if [ "$code" != 000 ]; then ok "mcp listening on :$port"; else warn "mcp is not answering; see 'systemctl --user status $MCP_UNIT'"; fi
  fi
fi

trap - ERR
printf '\n%s%s✓ done%s  relaunch any open %sgrove%s TUI to pick up the new code.\n' \
  "$BOLD" "$GREEN" "$OFF" "$BOLD" "$OFF"
