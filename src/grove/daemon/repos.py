"""Back-compat re-export of ``RepoRegistry``.

The registry moved to :mod:`grove.core.registry` once a second inward consumer
(the ``ActivityService``) needed it — it is pure engine, so it belongs in core
and dependencies flow inward. This module keeps ``grove.daemon.repos.RepoRegistry``
importable for the daemon and its tests, which legitimately compose the registry.
New code should import from ``grove.core.registry``.
"""

from __future__ import annotations

from grove.core.registry import RepoRegistry

__all__ = ["RepoRegistry"]
