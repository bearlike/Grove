"""Parsing a human-typed ticket or pull-request reference into a selector.

One class, :class:`TicketLink`, turns the four shapes a person actually types —
a browser URL (``https://host/owner/repo/issues/42``, ``.../pull/301``), a bare
``#42``, a bare ``42``, and ``owner/repo#42`` — into the *hints* needed to name
exactly one ticket: the id, the ``kind`` (``issues`` vs ``pull``/``pulls`` in
the path), and optionally the host and ``owner/repo`` the text qualified it
with. Linear's keyed form (``ENG-123``) parses too, for free: the id grammar is
the only thing that has to admit it.

**Parsing is segmentation; validation is Pydantic's.** ``parse`` only decides
*which* form the text is and pulls the pieces out; every constraint on those
pieces (the id grammar, the ``owner/repo`` shape, the host charset, the
lowercasing) lives on the fields as validators, so the rules are stated once at
the point of definition rather than duplicated across four regex branches. A
piece that fails them is a :class:`TicketLinkError`, never a ``ValidationError``
leaking out of the tickets layer.

**Resolution is deliberately NOT here.** A link's hints are matched against the
*enabled* providers by :meth:`TicketProviderRegistry.resolve_link` — the
registry is the one thing that knows which providers exist, exactly as it is for
branch-ref aggregation. This class only answers, per provider,
:meth:`candidate`: "could this link name a ticket in you?" A bare number with
two numeric trackers enabled answers yes twice, and the registry raises rather
than guessing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from grove.core.contracts.tickets import TicketKind
from grove.core.errors import TicketLinkError

if TYPE_CHECKING:
    from grove.core.tickets.provider import TicketProvider

_FROZEN = ConfigDict(extra="forbid", frozen=True)

# `owner/repo#42` and the bare `#42` / `42` / `ENG-123` forms. Segmentation
# only — the captured pieces are validated by the fields below.
_QUALIFIED = re.compile(r"^([^\s/#]+/[^\s/#]+)#(\S+)$")
_BARE = re.compile(r"^#?(\S+)$")

# The path segment a forge uses for each kind. Gitea browser URLs say `pulls`,
# GitHub's say `pull`; both say `issues`.
_PATH_KINDS: dict[str, TicketKind] = {
    "issues": "issue",
    "pull": "pull_request",
    "pulls": "pull_request",
}


class TicketLink(BaseModel):
    """A parsed reference to one ticket, plus whatever the text qualified it with.

    ``id`` and ``kind`` are what the caller ultimately wants; ``host`` and
    ``repo`` are hints that *narrow* which provider may own the reference and
    are ``None`` for an unqualified ``#42``. Nothing here is persisted or put on
    the wire — it is the intermediate between raw text and a ``TicketSelector``.
    """

    model_config = _FROZEN

    id: str = Field(pattern=r"^(?:[0-9]+|[A-Za-z][A-Za-z0-9]*-[0-9]+)$")
    """A bare number (Gitea/GitHub) or a keyed ``TEAM-123`` (Linear). One
    grammar for every tracker, so no branch here has to know which is which."""

    kind: TicketKind = "issue"
    repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    host: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.-]+$")

    @field_validator("host", "repo", mode="before")
    @classmethod
    def _casefold(cls, value: str | None) -> str | None:
        """Hosts and ``owner/repo`` compare case-insensitively on every forge."""
        return value.lower() if isinstance(value, str) else value

    # ─── parsing (segmentation only — the fields above do the validating) ────

    @classmethod
    def parse(cls, text: str) -> TicketLink:
        """Read one reference out of human input, or raise :class:`TicketLinkError`."""
        raw = text.strip()
        if not raw:
            raise TicketLinkError("empty ticket reference")
        try:
            if "://" in raw:
                return cls._from_url(raw)
            qualified = _QUALIFIED.match(raw)
            if qualified is not None:
                return cls(repo=qualified.group(1), id=qualified.group(2))
            bare = _BARE.match(raw)
            if bare is not None:
                return cls(id=bare.group(1))
        except ValidationError as exc:
            # The shape matched but a piece failed its field rule (a non-numeric
            # id, a repo with a space). Narrow it: a ValidationError must not
            # escape the tickets layer.
            raise TicketLinkError(f"{text!r} is not a valid ticket reference") from exc
        raise TicketLinkError(
            f"{text!r} is not a ticket reference (expected a URL, '#42', '42', or 'owner/repo#42')"
        )

    @classmethod
    def _from_url(cls, raw: str) -> TicketLink:
        """Split a browser URL into host / owner-repo / kind / number.

        Scans the path for a ``issues``/``pull``/``pulls`` segment followed by a
        number, taking the two segments before it as ``owner/repo`` — rather
        than anchoring one regex at the path root, so a Gitea instance served
        under a subpath and a deep link (``.../pull/301/files``) both parse.
        """
        parts = urlsplit(raw)
        segments = [s for s in parts.path.split("/") if s]
        for index, segment in enumerate(segments):
            kind = _PATH_KINDS.get(segment.lower())
            if kind is None or index < 2 or index + 1 >= len(segments):
                continue
            return cls(
                id=segments[index + 1],
                kind=kind,
                repo=f"{segments[index - 2]}/{segments[index - 1]}",
                host=parts.hostname or None,
            )
        raise TicketLinkError(
            f"{raw!r} is not a ticket URL (expected .../owner/repo/issues|pull/<number>)"
        )

    # ─── matching (pure; the registry supplies the enabled providers) ────────

    def candidate(self, provider: TicketProvider) -> bool:
        """Could this link name a ticket in ``provider``?

        A hint only ever *excludes*: a host or ``owner/repo`` the provider
        demonstrably does not serve rules it out, and an unset provider scope
        (no owner/repo configured) cannot rule anything out. With no hints at
        all — the bare ``#42`` / ``ENG-123`` case — the question collapses to
        "does this tracker's own key grammar claim the id", which is exactly
        what its pure branch parser already answers; there is no second copy of
        the numeric-vs-keyed rule here.
        """
        if self.host is not None and provider.host is not None and self.host != provider.host:
            return False
        scope = provider.context.lower() if provider.context is not None else None
        if self.repo is not None and scope is not None and self.repo != scope:
            return False
        if self.host is None and self.repo is None:
            return bool(provider.parse_branch_refs(self.id))
        return True


__all__ = ["TicketLink"]
