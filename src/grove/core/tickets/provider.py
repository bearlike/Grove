"""The ticket-provider interface and its HTTP plumbing base classes.

One ``TicketProvider`` Protocol, implemented once per tracker and reused by
every client (TUI / API / MCP) through the :class:`TicketProviderRegistry` — no
client re-parses or re-links. A provider has two faces:

* **pure** — ``parse_branch_refs`` / ``format_branch_name`` translate between a
  branch name and canonical ticket keys with no I/O. These are the engine's
  only entry point (the branch name is the source of truth); they must never
  touch the network so ``create()`` stays deterministic and offline-safe.
* **I/O** — ``list_assigned`` / ``get_ticket`` reach the tracker's REST/GraphQL
  API, and so do the comment-thread methods below. Side effects at the edge:
  only the daemon's ``/tickets`` routes and the issue-ops engine call these,
  never the lifecycle engine.

``HttpTicketProvider`` carries the shared wire plumbing (lazy ``httpx`` client,
one typed-error ``_request``) exactly as ``core/mewbo.py`` does. Concrete
providers normalize *shape* into the neutral :class:`TicketRef`, never semantics
(the provider-boundary rule). ``NumberTicketProvider`` factors the branch
grammar shared by the two numeric trackers (Gitea, GitHub); Linear's keyed
grammar lives in its own class.

Comment-thread I/O (``list_comments`` / ``post_comment`` / ``edit_comment`` /
``react``) is a capability only Gitea and GitHub back today.
``HttpTicketProvider`` gives all four a concrete base default that raises
:class:`TicketCommentsUnsupported` — a NotImplementedError-style default, but
narrowed to the tickets error family so callers handle one exception type
rather than a bare ``NotImplementedError``. Gitea/GitHub override the four;
Linear inherits the default untouched, so a non-implementing provider needs no
per-method stub. ``get_pull_request`` follows the identical recipe with its own
:class:`TicketPullRequestsUnsupported` sibling — a tracker can back comments and
still have no pull requests, so one capability must not answer for the other.

Assignee writes (``assign_self`` / ``unassign_self``) follow the identical
base-default recipe with their own :class:`TicketAssigneesUnsupported` sibling.
They are the outbound half of the assignee work queue: the account the
configured token authenticates as IS the bot, so "assign the bot" is
"assign myself" and needs no login configured anywhere. The two forges disagree
about the API SHAPE — GitHub has an additive assignees sub-resource, Gitea only
a whole-list PATCH that must be read-modify-written — and that difference lives
in the concrete providers, never in a caller.

Three capability properties round out the interface, all answered here once:
``can_comment`` and ``can_assign`` (capability AND credential — what a caller
picking a mirrorable/assignable ref must gate on) and ``host`` (the browser host
a pasted link is matched against, derived from the configured API root).

Credentials are resolved at the moment of use, never at construction — see
``HttpTicketProvider`` below and :mod:`grove.core.tickets.credentials` for why
the timing is the load-bearing part.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import quote, urlsplit

import httpx
from loguru import logger

from grove.core.contracts.tickets import (
    TicketComment,
    TicketProviderName,
    TicketReactionKind,
    TicketRef,
)
from grove.core.errors import (
    TicketAssigneesUnsupported,
    TicketCommentsUnsupported,
    TicketProviderError,
    TicketPullRequestsUnsupported,
)
from grove.core.workspace import slug


@dataclass(frozen=True, slots=True)
class TicketThread:
    """One ticket plus everything a reader would need to act on it: body + comments.

    A plain dataclass, not a contract model, and the test is the usual one: no
    non-Python client ever constructs or receives this. It exists because
    :class:`TicketRef` deliberately carries only *display* enrichment — a title,
    a url, a state — while an agent handed a ticket needs the description and the
    discussion, which are unbounded text nobody wants persisted on a workspace
    record or streamed on every dashboard tick.

    ``comments`` is in the tracker's own order (oldest first on both forges),
    because the whole point of rendering a thread is that the argument developed
    in that order.
    """

    ref: TicketRef
    body: str
    comments: tuple[TicketComment, ...] = ()


@runtime_checkable
class TicketProvider(Protocol):
    """The single interface every tracker implements and every client consumes."""

    # ClassVar so concrete providers can pin these as class attributes (they are
    # per-class constants, not per-instance) and still satisfy the Protocol.
    name: ClassVar[TicketProviderName]
    label: ClassVar[str]

    @property
    def configured(self) -> bool:
        """True when a credential is present so the I/O methods can succeed."""
        ...

    @property
    def context(self) -> str | None:
        """Human-readable scope (``owner/repo`` or a Linear team key), or None."""
        ...

    @property
    def host(self) -> str | None:
        """The browser host this tracker serves, for matching a pasted URL."""
        ...

    @property
    def can_comment(self) -> bool:
        """True when a comment call would actually reach the thread.

        Capability AND credential: a provider with no comment implementation
        (Linear) and an enabled-but-tokenless one are both False. Callers that
        pick "the first ref we can mirror onto" must gate on this — an enabled
        provider that cannot comment otherwise shadows every later ref.
        """
        ...

    @property
    def can_assign(self) -> bool:
        """True when an assignee write would actually reach the tracker.

        The same capability-AND-credential fold as :attr:`can_comment`, and a
        genuinely different question: assigning needs repo write, which
        commenting does not. A caller gates on this and treats ``False`` as "say
        so in the log, then carry on" — an unassignable tracker must never fail
        the work it was going to describe.
        """
        ...

    def viewer_login(self) -> str:
        """The account this provider's credential authenticates as. Network I/O.

        The token IS the bot: "assign Grove" means "assign whoever this token
        is", so no deployment names a bot account in config and no config can
        drift from the credential actually in use.
        """
        ...

    def assign_self(self, ticket_id: str) -> None:
        """Add :meth:`viewer_login` to the ticket's assignees. Network I/O.

        Additive and idempotent: an existing human assignee is never displaced,
        and assigning twice is a no-op. Deliberately has no completion inverse —
        Grove never unassigns when work lands, because the assignment IS the
        record of who did it.
        """
        ...

    def unassign_self(self, ticket_id: str) -> None:
        """Remove :meth:`viewer_login` from the ticket's assignees. Network I/O.

        The explicit hand-back, driven only by a human asking for it. Every
        other assignee is left untouched.
        """
        ...

    def read_thread(self, ticket_id: str) -> TicketThread:
        """The ticket's ref, description body and whole comment thread. Network I/O.

        One call for the caller, two round-trips underneath — the forges keep the
        body on the issue and the comments on a sub-resource.
        """
        ...

    def commit_url(self, sha: str) -> str | None:
        """Browser URL for one commit in this tracker's repo, or ``None``. Pure.

        ``None`` is a real answer, not a failure: a tracker that fronts no repo
        (Linear) has no such destination, and a caller must then render plain
        text. Manufacturing a link that lands nowhere is worse than no link —
        it spends the reader's click.
        """
        ...

    def branch_url(self, branch: str) -> str | None:
        """Browser URL for one branch's view in this tracker's repo, or ``None``. Pure.

        Same ``None`` contract as :meth:`commit_url`.
        """
        ...

    def parse_branch_refs(self, branch: str) -> list[str]:
        """Canonical ticket keys this provider recognizes in ``branch``. Pure."""
        ...

    def format_branch_name(self, ticket_id: str, title: str | None) -> str:
        """The ``<key>-<slug>`` branch stem for a ticket (no worktree prefix). Pure."""
        ...

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        """Tickets assigned to the authenticated user. Network I/O."""
        ...

    def get_ticket(self, ticket_id: str) -> TicketRef:
        """Fetch one ticket by its canonical key. Network I/O."""
        ...

    def get_pull_request(self, ticket_id: str) -> TicketRef:
        """Fetch one pull request by its number, from the tracker's pulls namespace.

        Returns the same neutral :class:`TicketRef` with ``kind="pull_request"``
        and ``status`` carrying the PR's real state (``merged`` is not
        ``closed``). Network I/O.
        """
        ...

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        """Every comment on the ticket thread, in provider order. Network I/O.

        Used to detect Grove's own marker comment on a cold start (issue-ops
        looks for its sticky comment before deciding to post vs. edit).
        """
        ...

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        """Post a new thread comment; the returned id anchors edit/react. Network I/O."""
        ...

    def edit_comment(self, comment_id: str, body: str) -> None:
        """Update an existing comment's body in place (sticky-comment update). Network I/O."""
        ...

    def react(self, comment_id: str, reaction: TicketReactionKind) -> None:
        """Add a reaction to a comment as a best-effort ack. Network I/O.

        Callers must swallow failures here — an ack is cosmetic, never a
        reason to fail the command that triggered it.
        """
        ...


class HttpTicketProvider(ABC):
    """Shared HTTP plumbing for a tracker bound to one configuration.

    Subclasses supply ``name`` / ``label``, the ``base_url``, the non-auth
    ``headers`` and the ``token_env`` name to look up, and implement the four
    interface methods. The ``httpx`` client is built lazily on the first I/O
    call, so a provider used only for pure branch parsing (the engine's path)
    never opens a socket.

    **The credential is resolved per use, never at construction.** The token used
    to be read in ``__init__`` and baked into an ``Authorization`` header, with
    ``configured`` frozen alongside it — and the registry holding this provider
    is cached on a ``WorkspaceManager`` that a daemon keeps for the whole
    process. A credential that appeared after that moment (a workspace init
    script writing a dotenv, a secret manager, a ``login``) could therefore never
    be seen. Now the token is looked up in ``env`` — a live mapping, in
    production a :class:`~grove.core.tickets.credentials.TicketEnv` that consults
    the configured ``tickets.env_file`` / ``env_command`` first and the process
    environment second — once per request, and the auth header is composed from
    it there rather than held on the client.
    """

    name: ClassVar[TicketProviderName]
    label: ClassVar[str]

    comments_supported: ClassVar[bool] = False
    """Declared by the subclasses that override the four comment methods below.
    Kept as a declaration rather than derived from "did this class override
    ``post_comment``" so the capability reads off the class like ``label`` does;
    a test pins the two in agreement so they cannot drift."""

    assignees_supported: ClassVar[bool] = False
    """Declared by the subclasses that override the assignee writes below. Same
    declaration-not-derivation rule as ``comments_supported``, and a SEPARATE
    flag: assigning needs repo write where commenting does not, so one must
    never answer for the other."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        token_env: str,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._headers = headers
        self._token_env = token_env
        self._env = env
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.Client | None = None

    @abstractmethod
    def auth_headers(self, token: str) -> dict[str, str]:
        """The tracker's auth header for a resolved token. Pure, shape only.

        Every tracker spells the same credential differently (``token <t>``,
        ``Bearer <t>``, bare) — provider *shape*, so it lives on the provider,
        composed per request rather than baked into the client's defaults.
        """

    def _token(self) -> str:
        """This provider's credential, resolved RIGHT NOW.

        The one read site: ``configured`` and every request go through here, so
        there is no second place to forget the timing rule.
        """
        return self._env.get(self._token_env, "")

    @property
    def configured(self) -> bool:
        """Does a credential resolve at this moment?

        A source that cannot be resolved at all (a missing ``env_file``, a
        failing ``env_command``) reads as *not configured* here rather than
        raising, because this property feeds render paths — the picker's
        gray-out flag and the daemon's "skip unconfigured providers" aggregation,
        where one broken tracker must not fail the whole request. The reason is
        logged, and an actual call to that provider still raises the typed error
        from :meth:`_request` instead of sending an unauthenticated request.
        """
        try:
            return bool(self._token())
        except TicketProviderError as exc:
            logger.warning("{} credential could not be resolved: {}", self.name, exc)
            return False

    @property
    def can_comment(self) -> bool:
        """Capability AND credential — see the Protocol member for why both."""
        return self.comments_supported and self.configured

    @property
    def can_assign(self) -> bool:
        """Capability AND credential — see the Protocol member for why both."""
        return self.assignees_supported and self.configured

    @property
    def host(self) -> str | None:
        """The browser host for this tracker, derived from the configured API root.

        A pasted URL carries the *browser* host while ``base_url`` is the API
        root, and GitHub is the one case where they differ by an ``api.``
        prefix (``api.github.com`` serves ``github.com``; a Gitea instance and a
        GitHub Enterprise ``/api/v3`` root already share their browser host).
        Stripping that prefix keeps the match generic — no provider name is
        hard-coded anywhere in the comparison.
        """
        hostname = urlsplit(self._base_url).hostname
        if not hostname:
            return None
        return hostname[4:] if hostname.startswith("api.") else hostname

    @property
    def web_root(self) -> str | None:
        """Scheme + browser authority for this tracker — the root of every link.

        :attr:`host` answers "which tracker does a pasted URL belong to", where
        a port is noise; a *built* URL needs it back, because a self-hosted
        forge on ``:3000`` is unreachable without it. Same ``api.``-stripping
        rule, so the two cannot disagree about which host serves the browser.
        """
        parts = urlsplit(self._base_url)
        host = self.host
        if not (parts.scheme and host):
            return None
        return f"{parts.scheme}://{host}{f':{parts.port}' if parts.port else ''}"

    def commit_url(self, sha: str) -> str | None:  # noqa: ARG002
        """No commit destination by default — see the Protocol member. Pure."""
        return None

    def branch_url(self, branch: str) -> str | None:  # noqa: ARG002
        """No branch destination by default — see the Protocol member. Pure."""
        return None

    @property
    @abstractmethod
    def context(self) -> str | None: ...

    @abstractmethod
    def parse_branch_refs(self, branch: str) -> list[str]: ...

    @abstractmethod
    def format_branch_name(self, ticket_id: str, title: str | None) -> str: ...

    @abstractmethod
    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]: ...

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> TicketRef: ...

    def get_pull_request(self, ticket_id: str) -> TicketRef:  # noqa: ARG002
        """Base default: this tracker has no pull-request concept.

        Same NotImplementedError-style default as the comment methods below,
        narrowed to its own typed error so callers keep handling one family.
        """
        raise TicketPullRequestsUnsupported(
            f"{self.name} provider does not implement get_pull_request"
        )

    # ─── comment I/O: NotImplementedError-style base default ───────────────
    #
    # Not @abstractmethod: only Gitea/GitHub override these. A provider that
    # doesn't (Linear) inherits the raise below untouched rather than needing
    # its own four-method stub.

    def list_comments(self, ticket_id: str) -> list[TicketComment]:  # noqa: ARG002
        raise self._comments_unsupported("list_comments")

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:  # noqa: ARG002
        raise self._comments_unsupported("post_comment")

    def edit_comment(self, comment_id: str, body: str) -> None:  # noqa: ARG002
        raise self._comments_unsupported("edit_comment")

    def react(self, comment_id: str, reaction: TicketReactionKind) -> None:  # noqa: ARG002
        raise self._comments_unsupported("react")

    def read_thread(self, ticket_id: str) -> TicketThread:  # noqa: ARG002
        """Base default: reading a thread needs the comment I/O this tracker lacks.

        Filed under the COMMENT capability rather than a fourth one of its own,
        because that is literally what it is short of — the description body
        rides the ticket read every provider already implements.
        """
        raise self._comments_unsupported("read_thread")

    def _comments_unsupported(self, op: str) -> TicketCommentsUnsupported:
        return TicketCommentsUnsupported(f"{self.name} provider does not implement {op}")

    # ─── assignee writes: the same base-default recipe, its own capability ───

    def viewer_login(self) -> str:
        raise self._assignees_unsupported("viewer_login")

    def assign_self(self, ticket_id: str) -> None:  # noqa: ARG002
        raise self._assignees_unsupported("assign_self")

    def unassign_self(self, ticket_id: str) -> None:  # noqa: ARG002
        raise self._assignees_unsupported("unassign_self")

    def _assignees_unsupported(self, op: str) -> TicketAssigneesUnsupported:
        return TicketAssigneesUnsupported(f"{self.name} provider does not implement {op}")

    # ─── wire plumbing (mirrors core/mewbo.py) ──────────────────────────────

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=self._headers,
                transport=self._transport,
            )
        return self._http

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """One wire round-trip; every failure mode narrows to TicketProviderError.

        Returns parsed JSON as-is (a dict or a list depending on the endpoint) —
        the concrete provider narrows it. ``httpx`` exceptions never escape.

        The credential is resolved ONCE here and used for both the gate and the
        header, so a request costs exactly one resolution — and a resolution that
        fails raises the typed error instead of being swallowed the way
        :attr:`configured` swallows it for a render path.
        """
        token = self._token()
        if not token:
            raise TicketProviderError(
                f"{self.name} provider has no credential "
                f"(set {self._token_env}, or point tickets.env_file/env_command at it); "
                f"cannot {method} {path}"
            )
        try:
            response = self._client().request(
                method, path, json=json_body, params=params, headers=self.auth_headers(token)
            )
        except httpx.HTTPError as exc:  # timeout, connect failure, protocol error
            raise TicketProviderError(f"{self.name} {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise TicketProviderError(
                f"{self.name} {method} {path} returned {response.status_code}: "
                f"{self._error_reason(response)}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise TicketProviderError(
                f"{self.name} {method} {path} returned malformed JSON"
            ) from exc

    @staticmethod
    def _error_reason(response: httpx.Response) -> str:
        """Best-effort human reason from a JSON error body, else a text snippet."""
        try:
            data = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(data, dict):
            for key in ("message", "error", "errors"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        return response.text[:200]

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _enrich_logged(self, ticket_id: str, exc: TicketProviderError) -> None:
        """Uniform debug log for a best-effort enrichment miss (callers swallow)."""
        logger.debug("ticket enrich failed for {} {}: {}", self.name, ticket_id, exc)


class NumberTicketProvider(HttpTicketProvider):
    """Branch grammar shared by trackers keyed by a bare issue number (Gitea, GitHub).

    Recognizes three forms of a reference, all anchored to a word boundary so a
    digit buried inside a slug (``fix-v2-bug``) is never mistaken for an id:

    * the **leading numeric segment** — ``123-slug`` or ``grove/123-slug`` (the
      number opens the branch or a path segment, then a dash or end);
    * a **built-in keyword** prefix — ``gh-123`` / ``gitea-123`` (per subclass);
    * the **configured** ``branch_prefix`` — whatever the repo sets.

    ``format_branch_name`` emits ``{branch_prefix}{id}-{slug}``; with an empty
    prefix that is the bare ``123-slug`` the leading-segment rule round-trips.
    The bare form is deliberately ambiguous across two enabled numeric providers
    — the registry marks such refs ``ambiguous`` rather than guessing.
    """

    builtin_prefixes: ClassVar[tuple[str, ...]] = ()

    branch_view_segment: ClassVar[str] = ""
    """The forge's path segment for a branch view — the ONE shape the two numeric
    forges spell differently (``src/branch`` on Gitea, ``tree`` on GitHub). The
    commit path is identical on both, so it stays here rather than duplicated per
    class. Empty means the tracker has no branch view and ``branch_url`` answers
    ``None``."""

    viewer_path: ClassVar[str] = ""
    """Where this forge reports the authenticated account (``/api/v1/user`` on
    Gitea, ``/user`` on GitHub). Shared here for the same reason the commit path
    is: one grammar, one differing segment, and an identical ``login`` key in the
    response — where ``_to_comment`` stays per-provider because JSON-shape
    normalization is a thing each adapter owns even when two shapes coincide.
    Empty falls back to the base default's raise."""

    def __init__(self, *, branch_prefix: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._branch_prefix = branch_prefix

    @property
    def repo_web_url(self) -> str | None:
        """This tracker's repo in the browser, or ``None`` when it isn't scoped to one."""
        root, scope = self.web_root, self.context
        return f"{root}/{scope}" if root and scope else None

    def commit_url(self, sha: str) -> str | None:
        repo = self.repo_web_url
        return f"{repo}/commit/{quote(sha, safe='')}" if repo and sha else None

    def branch_url(self, branch: str) -> str | None:
        repo = self.repo_web_url
        if not (repo and branch and self.branch_view_segment):
            return None
        # A branch name legitimately carries ``/`` as a path separator in the
        # forge's own URL; everything else is quoted so a name is never syntax.
        return f"{repo}/{self.branch_view_segment}/{quote(branch, safe='/')}"

    def parse_branch_refs(self, branch: str) -> list[str]:
        found: list[str] = []
        # Leading numeric segment: start-of-string or just after a '/'.
        for match in re.finditer(r"(?:^|/)(\d+)(?=-|$)", branch):
            found.append(match.group(1))
        # Keyword-prefixed anywhere, at a boundary: gh-123, gtea123, <prefix>123.
        keywords = [k for k in (*self.builtin_prefixes, self._branch_prefix.rstrip("-")) if k]
        for keyword in keywords:
            pattern = rf"(?<![A-Za-z0-9]){re.escape(keyword)}-?(\d+)(?![A-Za-z0-9])"
            for match in re.finditer(pattern, branch, flags=re.IGNORECASE):
                found.append(match.group(1))
        # Preserve first-seen order, drop duplicates (same id via two rules).
        return list(dict.fromkeys(found))

    def format_branch_name(self, ticket_id: str, title: str | None) -> str:
        stem = slug(title) if title else "ws"
        return f"{self._branch_prefix}{ticket_id}-{stem}"

    def viewer_login(self) -> str:
        """Who this token is, read from the forge itself. Network I/O."""
        if not self.viewer_path:
            raise self._assignees_unsupported("viewer_login")
        payload = self._request("GET", self.viewer_path)
        login = payload.get("login") if isinstance(payload, dict) else None
        if not login:
            raise TicketProviderError(
                f"{self.name} did not report an authenticated account for the configured token"
            )
        return str(login)

    @staticmethod
    def assignee_logins(issue: dict[str, Any]) -> list[str]:
        """Every assignee login on an issue payload, in the forge's own order.

        Distinct from ``_assignee`` (which answers the *display* question "who is
        on this", one name): a whole-list PATCH must round-trip the others
        untouched, so losing them to a first-match read would silently unassign
        the humans Grove is meant to work alongside.
        """
        logins: list[str] = []
        for entry in issue.get("assignees") or ():
            if isinstance(entry, dict) and entry.get("login"):
                logins.append(str(entry["login"]))
        return logins

    @staticmethod
    def with_login(logins: Sequence[str], login: str, *, present: bool) -> list[str] | None:
        """The assignee list with *login* added or removed, or ``None`` if unchanged.

        Pure, so the read-modify-write arm can decide whether a PATCH is worth
        sending without a second round-trip — ``None`` is what makes repeated
        assignment idempotent at the wire rather than only in effect.
        """
        current = list(logins)
        if present:
            return None if login in current else [*current, login]
        return [n for n in current if n != login] if login in current else None


__all__ = [
    "HttpTicketProvider",
    "NumberTicketProvider",
    "TicketProvider",
    "TicketThread",
]
