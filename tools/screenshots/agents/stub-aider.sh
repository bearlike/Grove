#!/bin/bash
# Demo stub posing as the `aider` agent for the screenshot pipeline.
# Same shape as stub-claude.sh: print once, then sleep so the pane stays.
set -euo pipefail
title="$(basename "$(pwd)")"
clear
# Workspace dir names carry a timestamp suffix; match prefix only.
case "$title" in
  flaky-test-fix*)
    cat <<'EOF'
aider> rg "flaky" --type py | head -5

tests/tui/test_peek_rail.py:124:    @pytest.mark.flaky(reruns=2)
tests/core/test_manager.py:391:    # flaky on macOS; needs a sleep
tests/tui/test_status_bar.py:42:    def test_flaky_pulse_skips...

aider> _
EOF
    ;;
  *)
    printf 'aider session for %s\n' "$title"
    ;;
esac
exec sleep 86400
