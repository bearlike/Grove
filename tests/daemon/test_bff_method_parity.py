"""The browser's proxy must relay every HTTP method the daemon serves.

This is a cross-language drift guard, and it exists because the failure it
catches is invisible from either side alone.

The webapp reaches the daemon through one Next.js catch-all route, and Next
dispatches by EXPORTED FUNCTION NAME: a file exporting `GET` and `POST` answers
405 to a `PUT` before any code in it runs. So when the daemon grew
`PUT /share-policy`, the daemon route was right, the typed client method was
right, the generated schema was right, and the browser still got a flat 405 --
with a stack trace pointing at the client, and an error naming a method the
daemon demonstrably supports. Nothing in the TypeScript project can see it:
there is no call site to type-check, because the missing thing is an export.

Python owns this test rather than vitest for one reason: the daemon's route
table is the source of truth for which methods exist, and only Python can
enumerate it. Reading the TS file from here is the same shape as the palette
drift tests that mirror a contracts module into the webapp -- the side that
KNOWS asserts against the side that must follow.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi.routing import APIRoute

from grove.core.config import load_config
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

#: The BFF that relays authenticated browser traffic to the daemon.
_PROXY = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "app"
    / "api"
    / "grove"
    / "[...path]"
    / "route.ts"
)

#: Next dispatches on the exported name, so this is exactly what it can answer.
_EXPORTED = re.compile(r"^export\s+async\s+function\s+([A-Z]+)\s*\(", re.MULTILINE)

#: Synthesized by Starlette alongside GET; never a surface anybody proxies.
_NOT_A_SURFACE = frozenset({"HEAD", "OPTIONS"})


def _daemon_methods() -> set[str]:
    with tempfile.TemporaryDirectory() as tmp:
        app = build_app(
            cfg=load_config(None),
            store=JsonWorkspaceStore(path=Path(tmp) / "state.json"),
        )
        methods: set[str] = set()
        for route in app.routes:
            if isinstance(route, APIRoute):
                methods |= set(route.methods) - _NOT_A_SURFACE
        return methods


def test_the_browser_proxy_relays_every_method_the_daemon_serves() -> None:
    assert _PROXY.is_file(), f"the BFF proxy moved; update this test's path: {_PROXY}"

    exported = set(_EXPORTED.findall(_PROXY.read_text())) - _NOT_A_SURFACE
    missing = _daemon_methods() - exported

    assert not missing, (
        "the daemon serves these methods but the browser's proxy exports no handler "
        f"for them, so every such request 405s before reaching any code: {sorted(missing)}. "
        f"Add `export async function <METHOD>` to {_PROXY.name}."
    )
