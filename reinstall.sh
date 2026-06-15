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
#   ./reinstall.sh --no-reinstall  # skip the uv reinstall (editable source is already live)
#   ./reinstall.sh --extras '.[daemon,mcp]'   # override the install extras (default '.[all]')
#   ./reinstall.sh -h | --help
#
# Surfaces it refreshes (see the reinstalling-grove skill for the full model):
#   • Package (CLI + TUI)  — `uv tool install --reinstall --editable`
#   • Webapp               — `make webapp-build` THEN restart the webapp service
#   • Daemon               — restart the long-lived HTTP + tmux service
#   • MCP (grove-mcp)      — nothing to restart; the client respawns it per connection
#
# Ports and service names are recovered live (never hard-coded) so the script is
# portable across hosts.

set -euo pipefail

# ─── options ──────────────────────────────────────────────────────────────────
DO_REINSTALL=1
DO_WEBAPP=1
DO_DAEMON=1
DO_VERIFY=1
EXTRAS=".[all]"

usage() {
  sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-reinstall) DO_REINSTALL=0 ;;
    --no-webapp)    DO_WEBAPP=0 ;;
    --no-daemon)    DO_DAEMON=0 ;;
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
[ "$DO_REINSTALL" = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_WEBAPP"    = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_DAEMON"    = 1 ] && TOTAL=$((TOTAL + 1))
[ "$DO_VERIFY"    = 1 ] && TOTAL=$((TOTAL + 1))

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
discover_service() {  # $1 = keyword (daemon|webapp) → echoes unit name or empty
  [ "$HAVE_SYSTEMCTL" = 1 ] || return 0
  systemctl --user list-unit-files "grove-*.service" --no-legend 2>/dev/null \
    | awk '{print $1}' | grep -E "grove-$1" | head -1
}
DAEMON_SVC="$(discover_service daemon)"
WEBAPP_SVC="$(discover_service webapp)"

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

# ─── 1. package (CLI + TUI) ───────────────────────────────────────────────────
if [ "$DO_REINSTALL" = 1 ]; then
  step "Reinstall package (editable) — extras ${EXTRAS}"
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
