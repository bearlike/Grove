"""A census of every daemon route that answers without a session.

This is the only assertion in the suite that can see the failure it exists to
catch, so it is worth saying plainly what that failure is.

Endpoint auth here is PER ROUTE — ``dependencies=auth_dep`` on each decorator —
rather than a global dependency on the app. That is a deliberate choice (the
pairing handshake and the liveness probe genuinely must answer without a
session, and `/public` now does too), and its cost is that **forgetting
``dependencies=auth_dep`` on a new route is a silent, total auth bypass for that
route**. Nothing catches it: the route type-checks, its own tests pass because
they were written by someone holding a token anyway, and the OpenAPI schema
records no difference a human would notice among two hundred paths.

The whole safety argument for public share links is that they live behind a
separate path prefix, so nothing under ``/workspaces`` can drift into being
world-readable. Nothing in the type system expresses that claim. This does.

**If this test fails, do not widen the expected set to make it pass.** Read the
diff it prints first and decide whether the new route was meant to be public.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

#: Every ``(method, path)`` the daemon serves without ``require_session``.
#:
#: Each entry is here for a stated reason, and an entry with no reason is a bug:
#:
#: - the two ``/auth/pair`` routes ARE the handshake by which a caller acquires
#:   a session, so requiring one would be circular;
#: - ``/healthz`` is the public liveness probe, and its ``version`` is already
#:   advertised in ``/openapi.json``'s ``info.version``;
#: - ``/hooks/agent-events`` is not unauthenticated at all — it carries its own
#:   ``require_hook_token`` dependency, because the agent posting to it is not a
#:   browser session;
#: - the three ``/public`` routes are the share namespace, authorized by the
#:   capability token in their own path;
#: - the ``/panel`` methods are the workspace-panel proxy, authorized the same
#:   way and for a reason the other routes do not have: a panel is rendered in an
#:   ``<iframe>`` and then upgraded to a WebSocket by the service behind it, and
#:   a browser attaches no ``Authorization`` header to either. The credential can
#:   therefore only live in the path. The token is HMAC-signed, expires, and
#:   names one workspace AND one panel, so it authorizes strictly less than a
#:   session: the route it guards can reach nothing but a compose service that
#:   workspace already declared. Like ``/public`` it sits under its own prefix so
#:   nothing beneath ``/workspaces`` can drift into being world-readable.
EXPECTED_UNAUTHENTICATED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/auth/pair"),
        ("GET", "/auth/pair/{challenge_id}"),
        ("GET", "/healthz"),
        ("POST", "/hooks/agent-events"),
        ("GET", "/public/{token}"),
        ("GET", "/public/{token}/turns"),
        ("GET", "/public/{token}/diff"),
        # Capability-scoped invalidations only; never the private fleet payload.
        ("GET", "/public/{token}/events"),
        ("GET", "/panel/{token}/{rest:path}"),
        ("POST", "/panel/{token}/{rest:path}"),
        ("PUT", "/panel/{token}/{rest:path}"),
        ("PATCH", "/panel/{token}/{rest:path}"),
        ("DELETE", "/panel/{token}/{rest:path}"),
        ("OPTIONS", "/panel/{token}/{rest:path}"),
    }
)


def _unauthenticated_routes(app: object) -> set[tuple[str, str]]:
    """Every ``(method, path)`` whose dependency tree lacks ``require_session``.

    Reads the resolved ``dependant`` rather than the decorator's kwargs, so a
    route that acquires the gate some other way (a router-level dependency, a
    handler parameter) still counts as authenticated — the question is whether
    the check RUNS, never how it was spelled.
    """
    found: set[tuple[str, str]] = set()
    for route in getattr(app, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        names = {getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies}
        if any("require_session" in name for name in names):
            continue
        # Mailboxes use a separate session dependency so scoped credentials
        # never gain the ordinary owner's authority. Keep this exception local
        # to their namespace; an ordinary route using it is a security defect.
        if route.path.startswith("/mailboxes/") and "require_mailbox_session" in names:
            continue
        for method in route.methods:
            # HEAD is synthesized alongside GET by Starlette and is not a
            # separate surface anybody can widen.
            if method != "HEAD":
                found.add((method, route.path))
    return found


def test_only_the_declared_routes_answer_without_a_session(tmp_path: object) -> None:
    app = build_app(
        cfg=GroveConfig(),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),  # type: ignore[operator]
    )
    actual = _unauthenticated_routes(app)

    # SET equality, never a subset check in either direction. "The public routes
    # are present" would pass while the entire workspace API went unauthenticated,
    # and "no unexpected routes" would pass if `/public` stopped being served at
    # all. Both halves of this comparison are load-bearing.
    unexpected = actual - EXPECTED_UNAUTHENTICATED
    missing = EXPECTED_UNAUTHENTICATED - actual
    assert not unexpected, (
        "these routes answer WITHOUT a session and are not in the expected census — "
        f"if that is intended, add them with a reason: {sorted(unexpected)}"
    )
    assert not missing, (
        "these routes were expected to be reachable without a session but now "
        f"require one: {sorted(missing)}"
    )
