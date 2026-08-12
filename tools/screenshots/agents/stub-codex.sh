#!/bin/bash
# Demo stub posing as the `codex` agent for the screenshot pipeline.
#
# Real `codex` is not installed in the screenshot environment. This stub
# prints plausible Codex-CLI-style output, then sleeps so the tmux pane
# keeps the content on screen for capture-pane. Picks the content from
# the worktree directory name, so each demo workspace shows different
# text while the agent name in Grove's config stays a single "codex"
# entry.
set -euo pipefail
title="$(basename "$(pwd)")"
clear
# Workspace dir names carry a timestamp suffix (slug-YYYYMMDD-HHMMSS), so
# match the slug prefix only.
case "$title" in
  rate-limiter*)
    cat <<'EOF'
codex-cli 0.147.0
▌ model: gpt-5.1-codex   approval: on-request   sandbox: workspace-write

> Add a token-bucket rate limiter to the API gateway middleware, 100 req/min per client.

• Ran pytest tests/core/http/test_rate_limit.py -q
  ⎿ 2 passed in 0.41s

⚡ Wiring TokenBucketLimiter into the middleware stack now.
EOF
    ;;
  empty-states*)
    cat <<'EOF'
codex-cli 0.147.0
▌ model: gpt-5.1-codex-mini   approval: on-request   sandbox: workspace-write

> Add an empty state to the workspace list when a project has zero workspaces.

• Applied patch: components/workspace/empty-state.tsx
• Ran npm test -- --watch=false src/components/workspace
  ⎿ 1 passed in 2.3s

Added the empty state component and a passing snapshot test.
EOF
    ;;
  *)
    printf 'codex session for %s\n' "$title"
    ;;
esac
exec sleep 86400
