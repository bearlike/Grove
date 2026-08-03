#!/usr/bin/env bash
# Phase runner for .devcontainer/init.d.
#
# WHY THIS EXISTS: the image (Tier 0) is expensive to rebuild, so anything that
# changes more often than the toolchain itself lives here instead. Steps run as
# ordinary scripts at container create/start, which means a dependency bump is a
# one-line edit rather than an image rebuild.
#
# Usage: bootstrap.sh create|start
#
# Every script in init.d/ is executed with GROVE_INIT_PHASE set, and decides for
# itself whether it participates in that phase. Scripts must be idempotent:
# `start` runs on every container start, including resume.
set -euo pipefail

phase="${1:-}"
case "$phase" in
	create | start) ;;
	*)
		echo "usage: bootstrap.sh create|start" >&2
		exit 2
		;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export GROVE_INIT_PHASE="$phase"
REPO_ROOT="$(cd "$here/.." && pwd)"
export REPO_ROOT

started=$SECONDS
count=0

for script in "$here"/init.d/*.sh; do
	[ -e "$script" ] || continue
	# A non-executable script is treated as deliberately parked, not an error.
	[ -x "$script" ] || continue

	echo "==> [$phase] $(basename "$script")"
	if ! bash "$script"; then
		status=$?
		echo "xx> FAILED: $(basename "$script") (exit $status)" >&2
		exit "$status"
	fi
	count=$((count + 1))
done

echo "==> [$phase] complete ($count scripts, $((SECONDS - started))s)"
