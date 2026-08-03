"""Where a ticket provider's token comes from, and WHEN it is read.

One question, one class. :class:`TicketEnv` is the mapping a provider looks its
``token_env`` up in — the same ``env`` seam the registry always had, except that
it now also consults the section's configured ``env_file`` / ``env_command`` and
does so **on every lookup** rather than once at construction.

The timing is the whole point. A provider used to read ``os.environ`` in its
``__init__``, and the registry holding it is cached on a ``WorkspaceManager``
that a daemon keeps for the life of the process — so a token that only exists
after the daemon started (written by a workspace init script, fetched from a
secret manager, refreshed by a ``login``) could never be seen, and a provider
constructed a second before the credential appeared stayed ``configured: false``
forever. Resolving at the moment of use fixes both halves at once: the read is
current, and caching the registry is safe again because nothing is captured.

Config still holds only NAMES and PATHS. A token value never appears in a config
model, in this module's repr, or in any message it produces.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path

from grove.core.config import TicketsConfig
from grove.core.env_source import EnvSource
from grove.core.errors import EnvSourceError, TicketProviderError


class TicketEnv(Mapping[str, str]):
    """The environment ticket credentials resolve against, read at the moment of use.

    A ``Mapping`` rather than a bespoke resolver so it drops into the seam that
    already existed: providers take ``env`` and look up their own ``token_env``,
    tests pass a plain ``dict``, and production passes one of these. Nothing had
    to learn a new interface for the timing to change.

    Lookup order, per read (documented on ``TicketsConfig`` for users):

    1. the section's configured source — ``tickets.env_command``'s stdout or
       ``tickets.env_file``'s contents, parsed as dotenv;
    2. the *base* mapping, which is the consuming process's own environment
       (``os.environ``, live) unless a caller injected one.

    The configured source wins because it is the more specific statement AND the
    one that can change under a long-lived process: an operator who points Grove
    at a credential file is saying "read it from here", and a stale value baked
    into the daemon's environment at exec must not shadow it.

    ``__getitem__`` can raise :class:`TicketProviderError` — unusual for a
    Mapping, and deliberate: a configured file that is missing or a command that
    fails is a real misconfiguration, not an absent key, and the tickets package
    narrows every failure to its own error family at its own boundary.
    """

    def __init__(
        self,
        cfg: TicketsConfig,
        *,
        repo_root: Path | None = None,
        base: Mapping[str, str] | None = None,
    ) -> None:
        self._cfg = cfg
        self._repo_root = repo_root
        # `os.environ` is a live view, so holding the object (not a copy) is
        # what keeps a lookup current for the ambient arm too.
        self._base: Mapping[str, str] = base if base is not None else os.environ

    def __repr__(self) -> str:
        """Never the values, and never the base mapping.

        The default would render the whole process environment — every secret it
        holds — through any incidental f-string or traceback frame dump. Same
        reasoning as ``EnvSource.__repr__``.
        """
        return f"TicketEnv(source={'yes' if self._has_source() else 'no'})"

    def __getitem__(self, key: str) -> str:
        source = self._resolve()
        if key in source:
            return source[key]
        return self._base[key]

    def __iter__(self) -> Iterator[str]:
        return iter({**self._base, **self._resolve()})

    def __len__(self) -> int:
        return len({**self._base, **self._resolve()})

    def _has_source(self) -> bool:
        """Is a source configured at all? The cheap path answers no."""
        return bool(self._cfg.env_file or self._cfg.env_command)

    def _resolve(self) -> Mapping[str, str]:
        """Read the configured source NOW; ``{}`` when none is configured.

        No cache, by the same argument the container arm records: a cache on a
        per-repo manager that lives for a daemon's whole life would hold secrets
        in memory for days and keep serving a rotated one — the failure this
        exists to fix, one layer over. An ``env_command`` is contracted to be
        idempotent and cheap, and a provider resolves once per request.

        Production always has a ``repo_root`` (the manager passes the repo whose
        cascade produced this config, so a repo-relative path means that repo).
        Without one — a registry built directly, as tests do — a relative path
        can only mean the process's own cwd, which is also what the command runs
        in.
        """
        if not self._has_source():
            return {}
        root = self._repo_root if self._repo_root is not None else Path.cwd()
        try:
            return EnvSource.resolve(self._cfg, repo_root=root).values
        except EnvSourceError as exc:
            raise TicketProviderError(f"ticket credentials could not be resolved: {exc}") from exc


__all__ = ["TicketEnv"]
