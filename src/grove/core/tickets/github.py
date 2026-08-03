"""GitHub Issues provider.

Wire facts (GitHub REST, ``api.github.com`` or an Enterprise ``/api/v3`` root):
auth is ``Authorization: Bearer <pat>`` plus the versioned ``Accept`` header;
``GET /issues?filter=assigned&state=open`` returns the authenticated user's
assigned issues across repos (scoped to ``owner/repo`` client-side);
``GET /repos/{owner}/{repo}/issues/{number}`` fetches one. The numeric ``number``
is the canonical key. Pull requests come back on the issues endpoint too — they
carry a ``pull_request`` member, which ``list_assigned`` drops so only real
issues reach the assigned listing (a PR arrives by explicit attach or branch
parse instead), and which ``_infer_kind`` reads so a single-issue read of a PR
number still reports ``kind="pull_request"``.

``GET /repos/{owner}/{repo}/pulls/{number}`` is the pulls namespace
``get_pull_request`` uses: only it carries the merge state, so a merged PR
normalizes to ``status="merged"`` rather than the ``closed`` the issues
endpoint reports.

Assignees: GitHub ships a dedicated ADDITIVE sub-resource —
``POST/DELETE /repos/{owner}/{repo}/issues/{number}/assignees`` with
``{"assignees": [login]}`` adds/removes without touching the others — so the
write is one round-trip here where Gitea needs a read-modify-write of its whole
``assignees`` array. That asymmetry is the forge difference this layer exists to
absorb. ``GET /user`` answers who the token is.

Comment I/O: ``GET/POST /repos/{owner}/{repo}/issues/{number}/comments``
lists/creates; ``PATCH /repos/{owner}/{repo}/issues/comments/{id}`` edits by
comment id alone; ``POST .../issues/comments/{id}/reactions`` with
``{"content": ...}`` reacts — no preview media type needed, reactions have
been GA on the stable REST API for years. The comment payload shape
(``id``/``body``/``user``/``created_at``) mirrors Gitea's, but is normalized
here rather than shared, matching this file's existing per-provider ``_to_ref``
precedent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from grove.core.config import GitHubTicketConfig
from grove.core.contracts.tickets import (
    TicketComment,
    TicketKind,
    TicketProviderName,
    TicketReactionKind,
    TicketRef,
)
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import NumberTicketProvider, TicketThread


class GitHubProvider(NumberTicketProvider):
    """GitHub Issues, bound to one ``GitHubTicketConfig``."""

    name: ClassVar[TicketProviderName] = "github"
    label: ClassVar[str] = "GitHub"
    builtin_prefixes: ClassVar[tuple[str, ...]] = ("gh",)
    comments_supported: ClassVar[bool] = True
    assignees_supported: ClassVar[bool] = True
    branch_view_segment: ClassVar[str] = "tree"
    viewer_path: ClassVar[str] = "/user"

    def __init__(
        self,
        cfg: GitHubTicketConfig,
        *,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=cfg.base_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            token_env=cfg.token_env,
            env=env,
            transport=transport,
            branch_prefix=cfg.branch_prefix,
        )
        self._owner = cfg.owner
        self._repo = cfg.repo

    def auth_headers(self, token: str) -> dict[str, str]:
        """GitHub's bearer scheme, composed per request."""
        return {"Authorization": f"Bearer {token}"}

    @property
    def context(self) -> str | None:
        return f"{self._owner}/{self._repo}" if self._owner and self._repo else None

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        params = {"filter": "assigned", "state": status or "open", "per_page": "50"}
        payload = self._request("GET", "/issues", params=params)
        issues = payload if isinstance(payload, list) else []
        scope = self.context
        refs: list[TicketRef] = []
        for issue in issues:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            if scope is not None and self._repo_full_name(issue) not in (scope, None):
                continue
            refs.append(self._to_ref(issue))
        return refs

    def get_ticket(self, ticket_id: str) -> TicketRef:
        owner, repo = self._scoped("fetch a ticket by id")
        path = f"/repos/{owner}/{repo}/issues/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"github returned an unexpected shape for issue {ticket_id}")
        return self._to_ref(payload)

    def get_pull_request(self, ticket_id: str) -> TicketRef:
        """One pull request from the ``pulls`` namespace — the PR-only metadata.

        ``merged`` is a boolean only the pulls endpoint sets; the issues
        endpoint's ``pull_request`` member carries just ``merged_at``. Either
        way a merged PR normalizes to ``status="merged"``, never ``closed``.
        """
        owner, repo = self._scoped("fetch a pull request by id")
        path = f"/repos/{owner}/{repo}/pulls/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"github returned an unexpected shape for pull {ticket_id}")
        return self._to_ref(payload, kind="pull_request")

    def read_thread(self, ticket_id: str) -> TicketThread:
        """The issue plus its whole comment thread — two GETs, one answer."""
        owner, repo = self._scoped("read a ticket thread")
        payload = self._request("GET", f"/repos/{owner}/{repo}/issues/{ticket_id}")
        if not isinstance(payload, dict):
            raise TicketProviderError(f"github returned an unexpected shape for issue {ticket_id}")
        return TicketThread(
            ref=self._to_ref(payload),
            body=payload.get("body") or "",
            comments=tuple(self.list_comments(ticket_id)),
        )

    # ─── assignee writes (one additive round-trip; no read needed) ───────────

    def assign_self(self, ticket_id: str) -> None:
        self._assignees(ticket_id, "POST")

    def unassign_self(self, ticket_id: str) -> None:
        self._assignees(ticket_id, "DELETE")

    def _assignees(self, ticket_id: str, method: str) -> None:
        """POST adds, DELETE removes — both leave every other assignee alone.

        No read-then-write here: GitHub's sub-resource is already additive, so
        re-assigning an account that is on the ticket is a no-op upstream.
        """
        owner, repo = self._scoped("change assignees")
        path = f"/repos/{owner}/{repo}/issues/{ticket_id}/assignees"
        self._request(method, path, json_body={"assignees": [self.viewer_login()]})

    # ─── comment I/O ────────────────────────────────────────────────────────

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        owner, repo = self._scoped("list comments")
        path = f"/repos/{owner}/{repo}/issues/{ticket_id}/comments"
        payload = self._request("GET", path)
        comments = payload if isinstance(payload, list) else []
        return [self._to_comment(c) for c in comments if isinstance(c, dict)]

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        owner, repo = self._scoped("post a comment")
        path = f"/repos/{owner}/{repo}/issues/{ticket_id}/comments"
        payload = self._request("POST", path, json_body={"body": body})
        if not isinstance(payload, dict):
            raise TicketProviderError("github returned an unexpected shape for a posted comment")
        return self._to_comment(payload)

    def edit_comment(self, comment_id: str, body: str) -> None:
        owner, repo = self._scoped("edit a comment")
        path = f"/repos/{owner}/{repo}/issues/comments/{comment_id}"
        self._request("PATCH", path, json_body={"body": body})

    def react(self, comment_id: str, reaction: TicketReactionKind) -> None:
        owner, repo = self._scoped("react to a comment")
        path = f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        self._request("POST", path, json_body={"content": reaction})

    # ─── shape normalization ────────────────────────────────────────────────

    def _scoped(self, op: str) -> tuple[str, str]:
        """(owner, repo) for a per-repo endpoint — raises if either is unset."""
        if not (self._owner and self._repo):
            raise TicketProviderError(f"github provider needs owner+repo configured to {op}")
        return self._owner, self._repo

    def _to_ref(self, issue: dict[str, Any], *, kind: TicketKind | None = None) -> TicketRef:
        return TicketRef(
            provider="github",
            id=str(issue.get("number", "")),
            kind=kind or self._infer_kind(issue),
            title=issue.get("title") or None,
            url=issue.get("html_url") or None,
            status=self._status(issue),
            assignee=self._assignee(issue),
        )

    @staticmethod
    def _infer_kind(issue: dict[str, Any]) -> TicketKind:
        """PR-ness off an *issues* payload: GitHub adds a ``pull_request`` key.

        The same discriminator ``list_assigned`` uses to drop PRs from the
        assigned listing — read once here so ``get_ticket`` on a PR number
        reports what it actually fetched instead of calling it an issue.
        """
        return "pull_request" if "pull_request" in issue else "issue"

    @staticmethod
    def _status(issue: dict[str, Any]) -> str | None:
        """``merged`` beats the bare ``open``/``closed`` state.

        ``merged`` is a boolean on the pulls payload; a PR read through the
        issues endpoint carries only ``pull_request.merged_at``. Both checked,
        since either shape can reach here.
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
        assignee = issue.get("assignee")
        if isinstance(assignee, dict) and assignee.get("login"):
            return str(assignee["login"])
        assignees = issue.get("assignees")
        if isinstance(assignees, list):
            for entry in assignees:
                if isinstance(entry, dict) and entry.get("login"):
                    return str(entry["login"])
        return None

    @staticmethod
    def _repo_full_name(issue: dict[str, Any]) -> str | None:
        # The list endpoint nests a `repository`; the single-issue endpoint
        # doesn't, but `repository_url` ends with `/repos/{owner}/{repo}`.
        repo = issue.get("repository")
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str):
            return str(repo["full_name"])
        url = issue.get("repository_url")
        if isinstance(url, str) and "/repos/" in url:
            return url.split("/repos/", 1)[1]
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


__all__ = ["GitHubProvider"]
