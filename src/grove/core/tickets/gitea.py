"""Gitea Issues provider — the first tracker (Grove's own issues live in Gitea).

Wire facts (Gitea REST ``/api/v1``): auth is ``Authorization: token <pat>``;
``GET /repos/issues/search?type=issues&assigned=true`` returns the authenticated
user's assigned issues across repos (scoped down to ``owner/repo`` client-side);
``GET /repos/{owner}/{repo}/issues/{index}`` fetches one. An issue carries
``number`` (the canonical key), ``title``, ``html_url``, ``state``
(``open``/``closed``), and ``assignees`` — normalized into the neutral
:class:`TicketRef` here, never reshaped for semantics.

Pull requests are the same numbering space, reachable at
``GET /repos/{owner}/{repo}/pulls/{index}`` — the only endpoint carrying the
merge state, so ``get_pull_request`` goes there and a merged PR normalizes to
``status="merged"``. The issues endpoint answers for a PR number too and flags
it (``is_pull`` on search results, a non-null ``pull_request`` member on the
single-issue read), so ``get_ticket`` reports an honest ``kind`` as well.
``list_assigned`` keeps its ``type=issues`` filter: a PR arrives by explicit
attach or branch parse, never by silently widening the assigned listing.

Assignees: Gitea exposes NO additive sub-resource — the only write is
``PATCH /repos/{owner}/{repo}/issues/{index}`` with an ``assignees`` array that
REPLACES the whole list. So an additive assign here is a read-modify-write: read
the issue, union the bot's login in, PATCH the union back. That is the forge
difference issue #419 warned about, and it belongs exactly here: the caller asks
for ``assign_self`` on both trackers and never learns that one of them needs two
round-trips. ``GET /api/v1/user`` answers who the token is.

Comment I/O: ``GET/POST /repos/{owner}/{repo}/issues/{index}/comments``
lists/creates; ``PATCH /repos/{owner}/{repo}/issues/comments/{id}`` edits by
comment id alone (Gitea comment ids are unique repo-wide, no issue index
needed); ``POST .../issues/comments/{id}/reactions`` with ``{"content": ...}``
reacts. A comment payload carries ``id``, ``body``, ``user``, ``created_at`` —
the same shape GitHub's comments API returns, but normalized here rather than
shared with :mod:`github`, matching this file's existing per-provider
``_to_ref`` precedent (an adapter owns its own JSON shape even when two
trackers happen to overlap).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from grove.core.config import GiteaTicketConfig
from grove.core.contracts.tickets import (
    TicketComment,
    TicketKind,
    TicketProviderName,
    TicketReactionKind,
    TicketRef,
)
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import NumberTicketProvider, TicketThread


class GiteaProvider(NumberTicketProvider):
    """Gitea Issues, bound to one ``GiteaTicketConfig``."""

    name: ClassVar[TicketProviderName] = "gitea"
    label: ClassVar[str] = "Gitea"
    builtin_prefixes: ClassVar[tuple[str, ...]] = ("gitea", "gtea")
    comments_supported: ClassVar[bool] = True
    assignees_supported: ClassVar[bool] = True
    body_supported: ClassVar[bool] = True
    branch_view_segment: ClassVar[str] = "src/branch"
    viewer_path: ClassVar[str] = "/api/v1/user"

    def __init__(
        self,
        cfg: GiteaTicketConfig,
        *,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=cfg.base_url.rstrip("/"),
            headers={"Accept": "application/json"},
            token_env=cfg.token_env,
            env=env,
            transport=transport,
            branch_prefix=cfg.branch_prefix,
        )
        self._owner = cfg.owner
        self._repo = cfg.repo

    def auth_headers(self, token: str) -> dict[str, str]:
        """Gitea's PAT scheme (``token <pat>``), composed per request."""
        return {"Authorization": f"token {token}"}

    @property
    def context(self) -> str | None:
        return f"{self._owner}/{self._repo}" if self._owner and self._repo else None

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        params = {
            "type": "issues",
            "state": status or "open",
            "assigned": "true",
            "limit": "50",
        }
        payload = self._request("GET", "/api/v1/repos/issues/search", params=params)
        issues = payload if isinstance(payload, list) else []
        scope = self.context
        refs: list[TicketRef] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if scope is not None and self._repo_full_name(issue) not in (scope, None):
                continue
            refs.append(self._to_ref(issue))
        return refs

    def get_ticket(self, ticket_id: str) -> TicketRef:
        owner, repo = self._scoped("fetch a ticket by id")
        path = f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for issue {ticket_id}")
        return self._to_ref(payload)

    def get_pull_request(self, ticket_id: str) -> TicketRef:
        """One pull request from the ``pulls`` namespace — the PR-only metadata.

        The issues endpoint answers for a PR number too, but only the pulls
        endpoint carries the merge state, which is the whole reason to come
        here: a merged PR is not a closed one.
        """
        owner, repo = self._scoped("fetch a pull request by id")
        path = f"/api/v1/repos/{owner}/{repo}/pulls/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for pull {ticket_id}")
        return self._to_ref(payload, kind="pull_request")

    def read_thread(self, ticket_id: str) -> TicketThread:
        """The issue plus its whole comment thread — two GETs, one answer."""
        owner, repo = self._scoped("read a ticket thread")
        payload = self._request("GET", f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}")
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for issue {ticket_id}")
        return TicketThread(
            ref=self._to_ref(payload),
            body=payload.get("body") or "",
            comments=tuple(self.list_comments(ticket_id)),
        )

    # ─── body read/write ────────────────────────────────────────────────────

    def read_body(self, ticket_id: str) -> str:
        owner, repo = self._scoped("read a ticket body")
        payload = self._request("GET", f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}")
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for issue {ticket_id}")
        return str(payload.get("body") or "")

    def update_body(self, ticket_id: str, body: str) -> None:
        owner, repo = self._scoped("update a ticket body")
        path = f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}"
        self._request("PATCH", path, json_body={"body": body})

    # ─── assignee writes (read-modify-write; Gitea has no additive endpoint) ─

    def assign_self(self, ticket_id: str) -> bool:
        return self._rewrite_assignees(ticket_id, present=True)

    def unassign_self(self, ticket_id: str) -> None:
        self._rewrite_assignees(ticket_id, present=False)

    def _rewrite_assignees(self, ticket_id: str, *, present: bool) -> bool:
        """PATCH the whole assignee list with the bot's login added or removed.

        The read is what keeps this additive: Gitea's ``assignees`` field
        replaces, so PATCHing ``[bot]`` blind would evict every human on the
        ticket. An unchanged list sends no PATCH at all, which is what makes a
        repeated assign free rather than merely harmless.

        Returns whether a PATCH was actually sent — i.e. whether this call is
        what changed the ticket, which the caller needs to tell "Grove assigned
        this" from "it was already assigned".
        """
        owner, repo = self._scoped("change assignees")
        login = self.viewer_login()
        path = f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for issue {ticket_id}")
        wanted = self.with_login(self.assignee_logins(payload), login, present=present)
        if wanted is None:
            return False
        self._request("PATCH", path, json_body={"assignees": wanted})
        return True

    # ─── comment I/O ────────────────────────────────────────────────────────

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        owner, repo = self._scoped("list comments")
        path = f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}/comments"
        payload = self._request("GET", path)
        comments = payload if isinstance(payload, list) else []
        return [self._to_comment(c) for c in comments if isinstance(c, dict)]

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        owner, repo = self._scoped("post a comment")
        path = f"/api/v1/repos/{owner}/{repo}/issues/{ticket_id}/comments"
        payload = self._request("POST", path, json_body={"body": body})
        if not isinstance(payload, dict):
            raise TicketProviderError("gitea returned an unexpected shape for a posted comment")
        return self._to_comment(payload)

    def edit_comment(self, comment_id: str, body: str) -> None:
        owner, repo = self._scoped("edit a comment")
        path = f"/api/v1/repos/{owner}/{repo}/issues/comments/{comment_id}"
        self._request("PATCH", path, json_body={"body": body})

    def react(self, comment_id: str, reaction: TicketReactionKind) -> None:
        owner, repo = self._scoped("react to a comment")
        path = f"/api/v1/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        self._request("POST", path, json_body={"content": reaction})

    # ─── shape normalization ────────────────────────────────────────────────

    def _scoped(self, op: str) -> tuple[str, str]:
        """(owner, repo) for a per-repo endpoint — raises if either is unset."""
        if not (self._owner and self._repo):
            raise TicketProviderError(f"gitea provider needs owner+repo configured to {op}")
        return self._owner, self._repo

    def _to_ref(self, issue: dict[str, Any], *, kind: TicketKind | None = None) -> TicketRef:
        return TicketRef(
            provider="gitea",
            id=str(issue.get("number", "")),
            kind=kind or self._infer_kind(issue),
            title=issue.get("title") or None,
            url=issue.get("html_url") or None,
            status=self._status(issue),
            draft=bool(issue.get("draft")),
            assignee=self._assignee(issue),
        )

    @staticmethod
    def _infer_kind(issue: dict[str, Any]) -> TicketKind:
        """PR-ness off an *issues* payload: Gitea flags it two ways.

        The search endpoint sets ``is_pull``; the single-issue endpoint carries a
        non-null ``pull_request`` member instead. ``get_pull_request`` passes
        ``kind`` explicitly rather than sniffing, since its endpoint already
        answered the question.
        """
        if issue.get("is_pull") or isinstance(issue.get("pull_request"), dict):
            return "pull_request"
        return "issue"

    @staticmethod
    def _status(issue: dict[str, Any]) -> str | None:
        """``merged`` beats the bare ``open``/``closed`` state.

        Gitea reports the merge on the PR payload directly and, for a PR read
        through the issues endpoint, on the nested ``pull_request`` member —
        both checked, since either shape can reach here.
        """
        meta = issue.get("pull_request")
        merged = issue.get("merged") or issue.get("merged_at")
        if isinstance(meta, dict):
            merged = merged or meta.get("merged") or meta.get("merged_at")
        if merged:
            return "merged"
        return issue.get("state") or None

    @staticmethod
    def _assignee(issue: dict[str, Any]) -> str | None:
        assignees = issue.get("assignees")
        if isinstance(assignees, list):
            for entry in assignees:
                if isinstance(entry, dict) and entry.get("login"):
                    return str(entry["login"])
        single = issue.get("assignee")
        if isinstance(single, dict) and single.get("login"):
            return str(single["login"])
        return None

    @staticmethod
    def _repo_full_name(issue: dict[str, Any]) -> str | None:
        repo = issue.get("repository")
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str):
            return str(repo["full_name"])
        return None

    @staticmethod
    def _to_comment(comment: dict[str, Any]) -> TicketComment:
        user = comment.get("user")
        return TicketComment(
            id=str(comment.get("id", "")),
            body=comment.get("body") or "",
            author=user.get("login") if isinstance(user, dict) else None,
            created_at=comment.get("created_at") or None,
        )


__all__ = ["GiteaProvider"]
