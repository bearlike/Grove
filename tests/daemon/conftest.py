"""Daemon-test helpers.

Existing daemon tests focus on workspace lifecycle, peek, attach, etc. —
not the auth gate. They run with ``cfg.auth.enabled = False`` so token
minting doesn't pollute every TestClient construction. The gate itself
is covered in detail by ``tests/daemon/test_auth_endpoints.py`` (real
pair flow + 401 on missing/invalid token + every existing endpoint
refuses unauthenticated when auth is on).
"""

from __future__ import annotations

from grove.core.config import GroveConfig


def daemon_test_config() -> GroveConfig:
    """``GroveConfig`` instance with HTTP auth disabled (test-only).

    Named with the ``daemon_`` prefix so pytest's collector does not
    mistake it for a test (any top-level ``def test_*`` would be picked
    up as a 0-arg test that returns a value, triggering a warning).
    """
    return GroveConfig.model_validate({"auth": {"enabled": False}})
