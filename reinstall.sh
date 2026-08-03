#!/usr/bin/env bash
# Grove dev reinstaller — refresh every surface from a local checkout.
#
# Unlike install.sh (the end-user PyPI/git installer), this script is for
# contributors running Grove out of an editable `uv tool` install. New code on
# disk does NOT change anything already running: a live process loaded its
# modules at startup, a long-lived service keeps the old build until restarted,
# and the webapp serves a prebuilt `.next`. So an update is never "just pull" —
# it is reinstall the package, rebuild the webapp, restart the services, then
# prove each surface loaded the new code.
#
# Usage:
#   ./reinstall.sh                 # do everything (package + webapp + services + verify)
#   ./reinstall.sh --no-webapp     # skip the webapp rebuild
#   ./reinstall.sh --no-daemon     # skip restarting the daemon
#   ./reinstall.sh --no-mcp        # skip restarting a networked grove-mcp service
#   ./reinstall.sh --no-reinstall  # skip the uv reinstall (editable source is already live)
#   ./reinstall.sh --extras '.[daemon,mcp]'   # override the install extras (default '.[all]')
#   ./reinstall.sh -h | --help
#
# Surfaces it refreshes (see the reinstalling-grove skill for the full model):
#   • Package (CLI + TUI)  — `uv tool install --reinstall --editable`
#   • Webapp               — `make webapp-build` THEN restart the webapp service
#   • Daemon               — restart the long-lived HTTP + tmux service
#   • MCP (grove-mcp)      — stdio: nothing to restart; the client respawns it per
#                            connection. A networked `--transport streamable-http`
#                            server is long-lived, so it IS restarted here when a
#                            grove-mcp unit exists — a stdio-only host simply has
#                            no unit to find. Skipping it silently was a real gap:
#                            a newly added tool stayed invisible to networked
#                            clients while every other surface reported success.
#
# Ports and service names are recovered live (never hard-coded) so the script is
# portable across hosts.

set -euo pipefail

# ─── options ──────────────────────────────────────────────────────────────────
DO_REINSTALL=1
DO_WEBAPP=1
DO_DAEMON=1
DO_MCP=1
DO_VERIFY=1
EXTRAS=".[all]"

usage() {
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-reinstall) DO_REINSTALL=0 ;;
    --no-webapp)    DO_WEBAPP=0 ;;
    --no-daemon)    DO_DAEMON=0 ;;
    --no-mcp)       DO_MCP=0 ;;
    --no-verify)    DO_VERIFY=0 ;;
    --extras)       EXTRAS="${2:?--extras needs a value}"; shift ;;
    --extras=*)     EXTRAS="${1#--extras=}" ;;
    -h|--help)      usage 0 ;;
    *) echo "unknown flag: $1" >&2; usage 2 ;;
  esac
  shift
done

# ─── logging ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_DIM=$'\033[2m'; C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_DIM=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_OFF=""
fi

STEP=0
TOTAL=0

ts() { date +%H:%M:%S; }
step() { STEP=$((STEP + 1)); printf '%s%s[%d/%d]%s %s%s%s\n' "$C_BOLD" "$C_BLUE" "$STEP" "$TOTAL" "$C_OFF" "$C_BOLD" "$1" "$C_OFF"; }
info() { printf '  %s%s%s %s\n' "$C_DIM" "$(ts)" "$C_OFF" "$1"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
die()  { printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$1" >&2; exit 1; }

# ─── locate repo + tools ──────────────────────────────────────────────────────
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" \
  || die "not inside a git checkout — run this from the Grove repo"
cd "$REPO_ROOT"

command -v uv >/dev/null 2>&1 || die "uv not on PATH — see install.sh"
HAVE_SYSTEMCTL=0
command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1 && HAVE_SYSTEMCTL=1

HEAD_SHA="$(git rev-parse --short HEAD)"
HEAD_SUBJECT="$(git log --oneline -1)"
printf '%s%sGrove reinstall%s  %srepo:%s %s  %shead:%s %s\n\n' \
  "$C_BOLD" "$C_BLUE" "$C_OFF" "$C_DIM" "$C_OFF" "$REPO_ROOT" "$C_DIM" "$C_OFF" "$HEAD_SUBJECT"

# Discover the installed grove-* user services (host-agnostic).
discover_service() {  # $1 = keyword (daemon|webapp|mcp) → echoes unit name or empty
  [ "$HAVE_SYSTEMCTL" = 1 ] || return 0
  systemctl --user list-unit-files "grove-*.service" --no-legend 2>/dev/null \
    | awk '{print $1}' | grep -E "grove-$1" | head -1
}
DAEMON_SVC="$(discover_service daemon)"
WEBAPP_SVC="$(discover_service webapp)"
# Only a NETWORKED grove-mcp is a long-lived process worth restarting. A stdio
# server is respawned by its client per connection, so it needs nothing here and
# correctly has no unit to find.
MCP_SVC="$(discover_service mcp)"

# Counted after discovery, not before: the MCP step exists only on a host that
# actually runs a networked server, and printing "[3/5]" for a step that will
# never run reads as a silent skip.
[ "$DO_REINSTALL" = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_WEBAPP"    = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_DAEMON"    = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_MCP" = 1 ] && [ -n "$MCP_SVC" ] && TOTAL=$((TOTAL + 1))
[ "$DO_VERIFY"    = 1 ] && TOTAL=$((TOTAL + 1))

restart_service() {  # $1 = unit name, $2 = human label
  local unit="$1" label="$2"
  if [ -z "$unit" ]; then
    warn "no $label systemd unit found — restart it however it runs on this host"
    return 0
  fi
  info "restarting $unit"
  systemctl --user restart "$unit" || die "failed to restart $unit"
  ok "$label restarted ($unit)"
}

# Resolve the daemon URL from grove's own config, falling back to the default.
daemon_url() {
  local url
  url="$(grove debug 2>/dev/null | sed -n 's/.*\(http:\/\/127\.0\.0\.1:[0-9]\+\).*/\1/p' | head -1)"
  echo "${url:-http://127.0.0.1:7421}"
}

# Run any grove entrypoint as another user — a root shell, `sudo claude` spawning
# grove-mcp out of .mcp.json — and CPython writes that run's __pycache__ into this
# shared venv as uid 0. uv must empty site-packages to reinstall, cannot unlink the
# foreign bytecode, and aborts with a bare "Permission denied" naming some innocent
# dependency. The cause is unguessable from that message, so name it here.
assert_tool_venv_ours() {
  local dir owner intruder
  dir="$(uv tool dir 2>/dev/null)/grove"
  [ -d "$dir" ] || return 0
  owner="$(id -un)"
  intruder="$(find "$dir" ! -user "$owner" -print -quit 2>/dev/null)"
  [ -n "$intruder" ] || return 0
  die "$(printf '%s\n      %s\n      %s' \
    "the grove venv holds files not owned by ${owner} — e.g. ${intruder#"${dir}/"}" \
    "cause: a grove entrypoint ran as another user (usually root); uv cannot delete its bytecode cache" \
    "fix:   sudo chown -R ${owner}: ${dir}   # then re-run this script")"
}

# ─── 1. package (CLI + TUI) ───────────────────────────────────────────────────
if [ "$DO_REINSTALL" = 1 ]; then
  step "Reinstall package (editable) — extras ${EXTRAS}"
  assert_tool_venv_ours
  info "uv tool install --reinstall --force --editable '${EXTRAS}'"
  uv tool install --reinstall --force --editable "$EXTRAS" 2>&1 | sed 's/^/    /'
  RESOLVED="$(grove version 2>/dev/null || echo '?')"
  ok "package reinstalled — grove ${RESOLVED}"
else
  info "skipping package reinstall (--no-reinstall); editable source is live on next launch"
fi

# ─── 2. webapp (rebuild THEN restart) ─────────────────────────────────────────
if [ "$DO_WEBAPP" = 1 ]; then
  step "Rebuild webapp (.next) + restart"
  info "make webapp-build  (npm ci + npm run build)"
  make webapp-build 2>&1 | sed 's/^/    /' || die "webapp build failed"
  if [ -f webapp/.next/BUILD_ID ]; then
    ok "build complete — BUILD_ID $(cat webapp/.next/BUILD_ID)"
  fi
  restart_service "$WEBAPP_SVC" "webapp"
else
  info "skipping webapp (--no-webapp)"
fi

# ─── 3. daemon (restart) ──────────────────────────────────────────────────────
if [ "$DO_DAEMON" = 1 ]; then
  step "Restart daemon"
  restart_service "$DAEMON_SVC" "daemon"
else
  info "skipping daemon restart (--no-daemon)"
fi

# ─── 3b. networked MCP server (restart) ───────────────────────────────────────
# The reinstall above swapped the tool venv this unit's entrypoint lives in, but
# a running process keeps the modules it already imported — so without this it
# happily serves the PREVIOUS build while every other surface reports success.
# The symptom is a newly added tool simply not existing for networked clients,
# which reads as an MCP client problem rather than a stale server.
if [ -n "$MCP_SVC" ]; then
  if [ "$DO_MCP" = 1 ]; then
    step "Restart networked MCP server"
    restart_service "$MCP_SVC" "mcp"
  else
    info "skipping mcp restart (--no-mcp) — $MCP_SVC still serves the previous build"
  fi
else
  info "no grove-mcp unit — stdio servers respawn per connection, nothing to restart"
fi

# ─── 4. verify ────────────────────────────────────────────────────────────────
if [ "$DO_VERIFY" = 1 ]; then
  step "Verify surfaces loaded the new code"
  URL="$(daemon_url)"

  # Daemon races its own bind for ~1-2s after restart — poll healthz.
  if [ "$DO_DAEMON" = 1 ] || [ -n "$DAEMON_SVC" ]; then
    info "polling ${URL}/healthz"
    code=""
    for i in $(seq 1 15); do
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "${URL}/healthz" || true)"
      [ "$code" = 200 ] && { ok "healthz 200 (${i}s)"; break; }
      sleep 1
    done
    [ "$code" = 200 ] || warn "healthz did not return 200 (last: ${code:-no response})"

    # Route surface: count paths + confirm a known endpoint is present.
    routes="$(curl -s --max-time 3 "${URL}/openapi.json" 2>/dev/null \
      | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['paths']), '/events' in d['paths'])" 2>/dev/null || true)"
    [ -n "$routes" ] && info "openapi routes: ${routes% *} (/events present: ${routes#* })"
  fi

  # CLI / config sanity.
  if grove debug >/dev/null 2>&1; then
    ok "grove debug OK (config loaded)"
  else
    warn "grove debug failed — check 'grove config show'"
  fi

  # MCP importability (only meaningful when the mcp extra is installed).
  if grove-mcp --help >/dev/null 2>&1; then
    ok "grove-mcp importable (mcp SDK resolved)"
  else
    warn "grove-mcp not importable — reinstall with an mcp extra (e.g. '.[all]')"
  fi

  # A networked MCP server is a surface like any other, so prove it came back
  # rather than assuming the restart took. Port is read off the unit's own
  # ExecStart — never hard-coded, same rule as the daemon URL above.
  if [ -n "$MCP_SVC" ]; then
    mcp_port="$(systemctl --user cat "$MCP_SVC" 2>/dev/null \
      | sed -n 's/.*--port[= ]\([0-9]\{1,\}\).*/\1/p' | head -1)"
    if [ "$(systemctl --user is-active "$MCP_SVC" 2>/dev/null)" = active ]; then
      if [ -n "$mcp_port" ]; then
        # Any HTTP status proves the listener is up; the MCP endpoint itself
        # rejects a bare GET (it wants POST + session headers), so a 4xx here is
        # a healthy server, not a failure. Only a connection refusal is bad.
        mcode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${mcp_port}/mcp" 2>/dev/null || true)"
        case "$mcode" in
          000|"") warn "mcp active but nothing answering on :${mcp_port}" ;;
          *)      ok "mcp serving on :${mcp_port} (HTTP ${mcode})" ;;
        esac
      else
        ok "mcp active ($MCP_SVC)"
      fi
    else
      warn "$MCP_SVC is not active — check 'systemctl --user status $MCP_SVC'"
    fi
  fi

  # Webapp HTTP reachability + build freshness.
  if [ -n "$WEBAPP_SVC" ] || [ -f webapp/.next/BUILD_ID ]; then
    wcode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:3000/ 2>/dev/null || true)"
    case "$wcode" in
      200|302|307) ok "webapp up (HTTP ${wcode})" ;;
      *)           warn "webapp not reachable on :3000 (HTTP ${wcode:-none}) — port may differ on this host" ;;
    esac
  fi
fi

printf '\n%s%s✓ done%s — refreshed from %s\n' "$C_BOLD" "$C_GREEN" "$C_OFF" "$HEAD_SUBJECT"
printf '%sNote:%s the TUI is a process YOU launch — quit any open %sgrove%s TUI and relaunch to pick up changes.\n' \
  "$C_DIM" "$C_OFF" "$C_BOLD" "$C_OFF"
