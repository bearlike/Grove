"""ASGI entrypoint for ``grove daemon serve``.

uvicorn imports this module by string ("grove.daemon._asgi:app") and
mounts the resulting FastAPI app. We construct it lazily here using the
loaded user config + the global JSON workspace store — module import IS
the wiring step.
"""

from __future__ import annotations

from grove.core.config import load_config
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

app = build_app(cfg=load_config(repo_root=None), store=JsonWorkspaceStore())
