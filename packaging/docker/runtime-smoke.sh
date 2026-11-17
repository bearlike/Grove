#!/usr/bin/env bash
# Runtime-image smoke: proves the image a remote workspace runs boots the
# native worker PANELESS under plain Docker, before any cloud driver exists.
#
#   make runtime-smoke
#
# Asserted, in order:
#   1. the image carries Grove, git and the agent CLI, and exposes no port
#   2. `grove-native-worker` starts with no tmux, prints its protocol-frame
#      wire log (the paneless pane) and sends the agent its first frame
#   3. it DIALS the daemon and backs off when none answers: nothing dials in
set -euo pipefail

IMAGE="${GROVE_RUNTIME_IMAGE:-grove-runtime:smoke}"
WORKSPACE_ID="$(printf 'a%.0s' {1..32})"
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

docker run --rm --entrypoint sh "$IMAGE" -c 'grove version && git --version && claude --version' \
    || fail "image is missing grove, git or the agent CLI"
ports="$(docker image inspect "$IMAGE" --format '{{len .Config.ExposedPorts}}')"
[ "$ports" = 0 ] || fail "image exposes $ports port(s); a remote workspace accepts no inbound"
! docker run --rm --entrypoint sh "$IMAGE" -c 'command -v tmux' >/dev/null \
    || fail "image ships tmux; the worker is paneless"

# Port 9 (discard) on the container's own loopback: no daemon is there.
config="{\"workspace_id\":\"$WORKSPACE_ID\",\"provider\":\"claude_code\",\"command\":[\"claude\"],\"daemon_url\":\"http://127.0.0.1:9\"}"
log="$(docker run --rm --entrypoint sh "$IMAGE" -c \
    "printf '%s' '$config' > /tmp/worker.json && timeout 15 grove-native-worker --config /tmp/worker.json" 2>&1 || true)"
grep -q 'Grove native session worker' <<<"$log" || fail "no wire-log banner:\n$log"
grep -q '→ {"type":"user"' <<<"$log" || fail "worker sent the agent no frame:\n$log"
grep -q 'daemon connection lost.*reconnecting' <<<"$log" || fail "worker did not dial the daemon:\n$log"
printf 'ok: %s boots the native worker paneless and dials out\n' "$IMAGE"
