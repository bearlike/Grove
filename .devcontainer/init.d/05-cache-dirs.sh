#!/usr/bin/env bash
# Ensures the shared cache-volume mount targets exist and are writable before
# any dependency install touches them. No phase guard: cheap, and a `start`
# after a volume was recreated externally must not assume `create` already
# did this.
#
# The volumes themselves are declared in devcontainer.json's `mounts`; the env
# vars (UV_CACHE_DIR, npm_config_cache, PLAYWRIGHT_BROWSERS_PATH) are set by
# devcontainer.json's containerEnv per the devcontainer contract's cache-volume
# table — this script only consumes them, it never invents a path.
set -euo pipefail

ensure_cache_dir() {  # $1 = env var name
	local var_name="$1" dir="${!1:-}"
	if [ -z "$dir" ]; then
		echo "-- skipped: ${var_name} not set"
		return 0
	fi
	mkdir -p "$dir"
	# Writability, not just existence: depending on the Docker volume driver a
	# freshly created mount can come up root-owned.
	if [ ! -w "$dir" ]; then
		echo "-- skipped: ${dir} not writable by $(id -un), leaving as-is"
		return 0
	fi
	echo "-- ok: ${dir} (${var_name})"
}

ensure_cache_dir UV_CACHE_DIR
ensure_cache_dir npm_config_cache
ensure_cache_dir PLAYWRIGHT_BROWSERS_PATH
