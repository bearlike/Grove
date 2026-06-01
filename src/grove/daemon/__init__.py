"""Grove HTTP daemon — thin FastAPI wrapper over ``grove.core``.

Public surface (re-exported here):
    build_app(cfg, store) -> FastAPI

Internal modules:
    repos    — RepoRegistry: lazy WorkspaceManager-per-repo cache
    app      — FastAPI factory + lifespan + route handlers
"""

from __future__ import annotations

from grove.daemon.app import build_app

__all__ = ["build_app"]
