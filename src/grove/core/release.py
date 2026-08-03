"""Best-effort "is a newer GitHub release out?" check.

Why this lives in the engine and not in each client: the version-skew check
is one concern with one network side effect, consumed by both the daemon
(→ ``WhoamiView`` → webapp) and the TUI footer. One implementation, one
bounded GitHub fetch per process per TTL, the semver comparison as a pure
unit. Grove's two-remote workflow puts releases on the GitHub remote, so
"newer release available" = the latest GitHub release tag is a higher version
than the installed :data:`grove.__version__`.

Side effects (the HTTPS GET) sit at the edge in :func:`fetch_latest_release_tag`
and the checker NEVER raises into a caller — the result feeds a render path, so
any failure logs and the last-good result (or "unknown") stands. The daemon
exposes the result on ``WhoamiView`` so the browser never polls GitHub itself;
the TUI is a separate process and calls the same checker in-process (same code,
its own bounded cache — not a second polling implementation).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger

from grove import __version__

# GitHub REST endpoint for the most recent published (non-draft, non-prerelease)
# release. Releases live on the GitHub mirror per the two-remote workflow.
_GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/{repo}/releases/latest"
_DEFAULT_REPO = "bearlike/Grove"
# Releases ship rarely; a long cache keeps GitHub's 60/hr unauthenticated budget
# untouched and means a wedged GitHub never stalls more than one render's worth.
_DEFAULT_TTL = timedelta(hours=6)
_FETCH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class _Version:
    """A parsed ``major.minor.patch`` release core, pre-release suffix dropped.

    Comparison zero-pads to equal width (``0.1`` == ``0.1.0``). We compare on
    the numeric core only — full PEP 440 / SemVer pre-release ordering is a
    refinement this feature does not need: treating ``0.2.0-rc1`` as ``0.2.0``
    only ever suppresses a slightly-too-eager nudge, never a wrong one.
    """

    release: tuple[int, ...]

    @classmethod
    def parse(cls, raw: str) -> _Version | None:
        """Parse a tag/version string, or ``None`` when it isn't numeric-dotted."""
        core = raw.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
        if not core:
            return None
        parts: list[int] = []
        for piece in core.split("."):
            if not piece.isdigit():
                return None
            parts.append(int(piece))
        return cls(tuple(parts)) if parts else None

    def exceeds(self, other: _Version) -> bool:
        """True when this version is strictly higher than ``other``."""
        width = max(len(self.release), len(other.release))
        mine = self.release + (0,) * (width - len(self.release))
        theirs = other.release + (0,) * (width - len(other.release))
        return mine > theirs


def _strip_leading_v(tag: str) -> str:
    """``v0.2.0`` → ``0.2.0``; leaves an already-bare version untouched."""
    tag = tag.strip()
    return tag[1:] if tag[:1] in "vV" else tag


def update_available(installed: str, latest: str | None) -> bool:
    """True when ``latest`` parses to a strictly higher release than ``installed``.

    Unknown or unparseable ``latest`` → ``False`` (never nudge on a bad read);
    equal or lower → ``False`` (a dev build ahead of the last release is fine).
    """
    if latest is None:
        return False
    installed_v = _Version.parse(installed)
    latest_v = _Version.parse(latest)
    if installed_v is None or latest_v is None:
        return False
    return latest_v.exceeds(installed_v)


@dataclass(frozen=True, slots=True)
class ReleaseStatus:
    """Outcome of a release check — in-process state, not a wire shape.

    ``latest`` is ``None`` until a successful fetch (offline / first call /
    error); ``update_available`` is ``False`` whenever ``latest`` is unknown.
    The daemon mirrors these onto ``WhoamiView`` for clients that can't run the
    check themselves (the browser).
    """

    installed: str
    latest: str | None
    update_available: bool


def fetch_latest_release_tag(repo: str, *, installed: str) -> str | None:
    """GET the latest release tag from GitHub, normalized to a bare version.

    The real network side effect (the edge). Returns the tag with any single
    leading ``v`` stripped (so ``v0.2.0`` → ``0.2.0``, symmetric with how
    clients render ``__version__``), or ``None`` when the payload has no usable
    ``tag_name``. Raises on transport/HTTP errors — :class:`ReleaseChecker`
    owns the best-effort boundary that swallows them.
    """
    url = _GITHUB_LATEST_RELEASE_URL.format(repo=repo)
    resp = httpx.get(
        url,
        timeout=_FETCH_TIMEOUT_SECONDS,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"grove/{installed}",
        },
    )
    resp.raise_for_status()
    tag = resp.json().get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None
    return _strip_leading_v(tag)


@dataclass(slots=True)
class ReleaseChecker:
    """Cached, best-effort newer-release check against the GitHub releases API.

    :meth:`check` returns a :class:`ReleaseStatus` without ever raising: a
    network or parse failure logs and yields the last-good result (or
    "unknown"). The GitHub GET fires at most once per ``ttl`` — releases change
    rarely, so a long cache costs nothing and a slow GitHub never stalls a
    render. The daemon and the TUI each hold one instance; ``fetcher`` + ``clock``
    are injectable so tests never touch the network.
    """

    installed: str = __version__
    repo: str = _DEFAULT_REPO
    ttl: timedelta = _DEFAULT_TTL
    # Injectable for tests; ``None`` → the real GitHub fetch resolved lazily so a
    # test patching the module-level seam takes effect (see tests/conftest.py).
    fetcher: Callable[[], str | None] | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _latest: str | None = field(default=None, init=False)
    _checked_at: datetime | None = field(default=None, init=False)

    def check(self) -> ReleaseStatus:
        """Return the cached status, refreshing from GitHub once the TTL lapses."""
        if self._is_stale():
            self._refresh()
        return ReleaseStatus(
            installed=self.installed,
            latest=self._latest,
            update_available=update_available(self.installed, self._latest),
        )

    def _is_stale(self) -> bool:
        if self._checked_at is None:
            return True
        return self.clock() - self._checked_at >= self.ttl

    def _refresh(self) -> None:
        """Re-fetch the latest tag, best-effort.

        A failed fetch keeps the last-good ``_latest`` and still advances
        ``_checked_at`` so a down GitHub is retried once per TTL rather than on
        every call (the cost of a transient failure is one TTL of staleness, an
        acceptable trade for never hammering the API from a render path).
        """
        try:
            tag = self.fetcher() if self.fetcher is not None else self._fetch()
        except Exception as exc:  # best-effort boundary — never raises; see docstring
            logger.warning("release check failed for {}: {}", self.repo, exc)
            self._checked_at = self.clock()
            return
        if tag is not None:
            # Normalize at the single store site so an injected fetcher and the
            # real GitHub fetch agree: ``latest`` is always a bare version
            # (``0.2.0``), symmetric with how clients render ``__version__``.
            self._latest = _strip_leading_v(tag)
            logger.debug("latest release for {} is {}", self.repo, self._latest)
        self._checked_at = self.clock()

    def _fetch(self) -> str | None:
        # Resolve the module seam at call time so a monkeypatch of
        # ``fetch_latest_release_tag`` (tests) is honored.
        return fetch_latest_release_tag(self.repo, installed=self.installed)


__all__ = ["ReleaseChecker", "ReleaseStatus", "fetch_latest_release_tag", "update_available"]
