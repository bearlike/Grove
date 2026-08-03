"""Back-compat re-export of ``RepoRegistry``.

The registry is pure engine (:mod:`grove.core.registry`) — the daemon is one of
several consumers, alongside ``ActivityService``, so dependencies flow inward
from here. This module keeps ``grove.daemon.repos.RepoRegistry`` importable for
the daemon and its tests, which legitimately compose the registry. New code
should import from ``grove.core.registry``.
"""

from __future__ import annotations

from grove.core.registry import RepoRegistry

__all__ = ["RepoRegistry"]
