# DELIBERATELY EMPTY, and it must stay that way.
#
# Both capture entrypoints live inside this package, so importing
# `tools.screenshots.capture` executes THIS file first — before the entrypoint's
# own `Sandbox(...).activate()` line has redirected HOME / XDG_CONFIG_HOME /
# CLAUDE_CONFIG_DIR / CODEX_HOME. Anything re-exported here that imports grove
# would therefore resolve a REAL profile path once, which is the single failure
# this sandbox exists to prevent.
#
# The public surface lives one level down instead: `fixture/` (pure, safe to
# import anywhere), `planter/` and `driver/`, each with its own `__all__`.
