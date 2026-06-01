#!/bin/bash
# Demo stub posing as the `claude` agent for the screenshot pipeline.
#
# Real `claude` is not installed in the screenshot environment. This stub
# prints realistic Claude-Code-style output, then sleeps so the tmux pane
# keeps the content on screen for capture-pane. Picks the content from
# the worktree directory name, so each demo workspace shows different
# text while the agent name in Grove's config stays a single "claude"
# entry.
set -euo pipefail
title="$(basename "$(pwd)")"
clear
# Workspace dir names carry a timestamp suffix (slug-YYYYMMDD-HHMMSS), so
# match the slug prefix only.
case "$title" in
  auth-refactor*)
    cat <<'EOF'
> Editing src/grove/core/auth/middleware.py

  The middleware now reads tokens from the request scope rather than
  the cookie jar. The legacy header path is gated behind the
  `legacy_header_token` feature flag so existing clients keep working.

  Updated 3 files (+47 / -12).

· Run the failing auth tests now? [y/n]
EOF
    ;;
  docs-rewrite*)
    cat <<'EOF'
> mkdocs build --strict

INFO    -  Cleaning site directory
INFO    -  Building documentation to directory: site
INFO    -  Documentation built in 1.43 seconds

build OK · 32 pages · 0 warnings
EOF
    ;;
  perf-bench*)
    cat <<'EOF'
> python -m grove.tools.bench --repeat 5

baseline (current):
  list      :  31.4 ms ± 0.6
  peek      :  18.7 ms ± 0.4

candidate (this branch):
  list      :  22.1 ms ± 0.5  (-30%)
  peek      :  12.8 ms ± 0.3  (-32%)
EOF
    ;;
  *)
    printf 'claude session for %s\n' "$title"
    ;;
esac
exec sleep 86400
