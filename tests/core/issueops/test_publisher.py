"""TicketStatusPublisher — the live sticky issue-comment (#197).

The publisher copies the ``NotificationBroker``'s discipline (CLAUDE.md): a pure
``render`` over a snapshot dataclass (tested with zero I/O), a coalescing fold
tested against a fake clock, and an end-to-end path driven by hand-built activity
deltas plus a capturing fake provider (the "stub only the I/O boundary" seam every
provider test uses). No threads, no network.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession, TodoItem, TodoList
from grove.core.config import IssueOpsConfig
from grove.core.contracts.issueops import IssueOpsEvent
from grove.core.contracts.tickets import TicketComment, TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.issueops import (
    SIGNATURE_MARKER,
    STICKY_MARKER,
    PublishSnapshot,
    TicketStatusPublisher,
)
from grove.core.issueops.publisher import _FlushJob
from grove.core.workspace import CommitSummary, WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 7, 9, 21, 38, 0, tzinfo=UTC)
_GITEA_REF = TicketRef(provider="gitea", id="42")


# ─── builders ────────────────────────────────────────────────────────────────


def _ws_state(*, refs: Sequence[TicketRef] = (_GITEA_REF,)) -> WorkspaceState:
    return WorkspaceState(
        id="ws1",
        title="fix-auth",
        repo_root="/home/u/proj",
        branch="grove/42-fix-auth",
        base_branch="main",
        worktree_path="/home/u/proj/.worktrees/fix-auth",
        tmux_session="grove-fix-auth",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=T0,
        updated_at=T0,
        ticket_refs=list(refs),
    )


def _row(
    state: AgentActivityState = AgentActivityState.WORKING,
    *,
    task: str | None = None,
    tool_calls: int = 0,
    commit: CommitSummary | None = None,
    refs: Sequence[TicketRef] = (_GITEA_REF,),
) -> WorkspaceActivity:
    session = SessionActivity(
        session=AgentSession(
            session_id="s1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(state=state, current_task=task, tool_calls=tool_calls),
    )
    return WorkspaceActivity(
        state=_ws_state(refs=refs),
        sessions=(session,),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(commit,) if commit else (),
        observed_at=T0,
    )


_SEQ = itertools.count(1)


def _delta(row: WorkspaceActivity) -> DashboardDelta:
    return DashboardDelta(
        kind="session_activity",
        seq=next(_SEQ),
        workspace_id=row.state.id,
        repo_root=row.state.repo_root,
        workspace=row,
    )


def _kill_delta() -> DashboardDelta:
    return DashboardDelta(
        kind="workspace_changed",
        seq=next(_SEQ),
        workspace_id="ws1",
        repo_root="/home/u/proj",
        detail={"event": "killed"},
    )


class _FakeProvider:
    """A capturing ``TicketProvider`` — records posts/edits, optionally raises."""

    name: TicketProviderName = "gitea"
    label = "Gitea"

    def __init__(self, *, comments: Sequence[TicketComment] = (), fail: bool = False) -> None:
        self.posts: list[tuple[str, str]] = []
        self.edits: list[tuple[str, str]] = []
        self.listed: list[str] = []
        self._comments = list(comments)
        self._fail = fail
        self._ids = itertools.count(1)

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        self.listed.append(ticket_id)
        return list(self._comments)

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        if self._fail:
            raise TicketProviderError("boom")
        comment = TicketComment(id=f"c{next(self._ids)}", body=body)
        self._comments.append(comment)
        self.posts.append((ticket_id, body))
        return comment

    def edit_comment(self, comment_id: str, body: str) -> None:
        if self._fail:
            raise TicketProviderError("boom")
        self.edits.append((comment_id, body))


def _publisher(
    provider: _FakeProvider | None,
    *,
    todo: TodoList | None = None,
    window: float = 5.0,
    clock: object | None = None,
    terminal_states: frozenset[AgentActivityState] = frozenset(),
) -> TicketStatusPublisher:
    return TicketStatusPublisher(
        config=IssueOpsConfig(
            enabled=True,
            update_window_seconds=window,
            deep_link_base_url="https://grove.example.com",
        ),
        provider_resolver=lambda root, name: (
            provider if provider is not None and name == provider.name else None
        ),
        todo_resolver=lambda root, ws_id: todo,
        clock=clock or (lambda: T0),
        terminal_states=terminal_states,
    )


# ─── pure render (zero I/O) ────────────────────────────────────────────────────


def _snapshot(**kw: object) -> PublishSnapshot:
    base: dict[str, object] = {
        "workspace_id": "ws1",
        "title": "fix-auth",
        "repo_name": "proj",
        "branch": "grove/42-fix-auth",
        "state": AgentActivityState.WORKING,
        "current_task": None,
        "todo": None,
        "latest_commit": None,
        "deep_link": "https://grove.example.com/w/ws1",
        "terminal": False,
        "occurred_at": T0,
    }
    base.update(kw)
    return PublishSnapshot(**base)  # type: ignore[arg-type]


def test_render_carries_both_markers() -> None:
    """The sticky footer stamps STICKY_MARKER (the unique cold-start recovery
    anchor) AND SIGNATURE_MARKER (the engine's ingest anti-loop guard)."""
    body = TicketStatusPublisher.render(_snapshot())
    assert STICKY_MARKER in body
    assert SIGNATURE_MARKER in body


def test_render_maps_todo_status_to_checkboxes() -> None:
    todo = TodoList(
        items=(
            TodoItem(content="write the parser", status="completed"),
            TodoItem(content="wire the daemon", status="in_progress"),
            TodoItem(content="add docs", status="pending"),
        )
    )
    body = TicketStatusPublisher.render(_snapshot(todo=todo))
    assert "- [x] write the parser" in body
    assert "- [ ] wire the daemon" in body
    assert "- [ ] add docs" in body


def test_render_includes_branch_commit_task_and_deep_link() -> None:
    commit = CommitSummary(sha="abc1234", subject="init parser", committed_at=T0)
    body = TicketStatusPublisher.render(
        _snapshot(current_task="parsing the branch", latest_commit=commit)
    )
    assert "grove/42-fix-auth" in body
    assert "abc1234" in body
    assert "init parser" in body
    assert "parsing the branch" in body
    assert "https://grove.example.com/w/ws1" in body


def test_render_omits_deep_link_when_absent() -> None:
    body = TicketStatusPublisher.render(_snapshot(deep_link=None))
    assert "http" not in body


def test_render_terminal_summary_is_a_final_outcome_not_a_live_checklist() -> None:
    todo = TodoList(items=(TodoItem(content="still-open", status="pending"),))
    commit = CommitSummary(sha="def5678", subject="final commit", committed_at=T0)
    body = TicketStatusPublisher.render(_snapshot(terminal=True, todo=todo, latest_commit=commit))
    assert SIGNATURE_MARKER in body
    assert "grove/42-fix-auth" in body
    assert "def5678" in body
    assert "https://grove.example.com/w/ws1" in body
    # A terminal comment is a final summary — it must not re-render the live
    # checklist a still-open item would show.
    assert "- [ ] still-open" not in body


# ─── coalescing (fake clock) ───────────────────────────────────────────────────


def test_coalesces_a_burst_into_one_flush_after_the_window() -> None:
    """Many render-relevant deltas inside one window fold into a single PATCH
    (the Sweep same-comment-storm lesson); the latest state wins."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="step-a", tool_calls=1)))
    pub.observe(_delta(_row(task="step-b", tool_calls=2)))
    pub.observe(_delta(_row(task="step-c", tool_calls=3)))
    assert provider.posts == []  # nothing until the window elapses
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1
    assert "step-c" in provider.posts[0][1]  # folded to the latest


def test_flush_before_the_window_elapses_is_a_noop() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=2))
    assert provider.posts == []


def test_render_irrelevant_churn_does_not_dirty_the_workspace() -> None:
    """A pure diff-stat change (dirty_files) the poll emits but the comment never
    renders must not schedule a redundant PATCH."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    row = _row(task="one", tool_calls=1)
    pub.observe(_delta(row))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1
    # Same render-relevant fingerprint (state/task/tool_calls/branch/commit) → clean.
    pub.observe(_delta(_row(task="one", tool_calls=1)))
    pub.flush_pending(now=T0 + timedelta(seconds=20))
    assert len(provider.posts) == 1
    assert provider.edits == []


def test_injected_terminal_state_flushes_immediately() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, terminal_states=frozenset({AgentActivityState.ERROR}))
    pub.observe(_delta(_row(AgentActivityState.ERROR)))
    assert len(provider.posts) == 1  # no window wait for a terminal state


# ─── the @grove status seam (flush_now / publish) ─────────────────────────────


def _event(**over: object) -> IssueOpsEvent:
    base: dict[str, object] = {
        "provider": "gitea",
        "owner": "acme",
        "repo": "widget",
        "issue_number": 42,
        "comment_id": "comment-100",
        "actor": "alice",
    }
    base.update(over)
    return IssueOpsEvent(**base)  # type: ignore[arg-type]


class _FakeManager:
    """A minimal manager exposing only the ``find_by_ticket`` seam ``publish`` uses."""

    def __init__(self, match_id: str | None) -> None:
        self._match_id = match_id
        self.queried: list[tuple[str, str]] = []

    def find_by_ticket(self, provider: str, ticket_id: str) -> object | None:
        self.queried.append((provider, ticket_id))
        return None if self._match_id is None else SimpleNamespace(id=self._match_id)


def test_flush_now_dispatches_the_latest_row_bypassing_the_window() -> None:
    """``@grove status`` forces an immediate re-render instead of waiting out the
    coalescing window."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="mid-flight")))
    assert provider.posts == []  # coalescing: nothing until the window elapses
    pub.flush_now("ws1")
    assert len(provider.posts) == 1
    assert "mid-flight" in provider.posts[0][1]


def test_flush_now_is_a_noop_for_an_unobserved_workspace() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.flush_now("never-seen")  # no cached row → nothing to render
    assert provider.posts == []


def test_publish_resolves_the_ticket_then_flushes_that_workspace() -> None:
    """The engine ``StatusPublisher`` seam: ticket → workspace via
    ``find_by_ticket``, then an immediate flush of that workspace's sticky comment."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="running")))
    manager = _FakeManager("ws1")
    pub.publish(_event(), manager)  # type: ignore[arg-type]
    assert manager.queried == [("gitea", "42")]  # stringified issue number
    assert len(provider.posts) == 1


def test_publish_is_a_noop_when_no_workspace_tracks_the_ticket() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="running")))
    pub.publish(_event(), _FakeManager(None))  # type: ignore[arg-type]
    assert provider.posts == []


# ─── end-to-end (in-memory deltas + capturing provider) ─────────────────────────


def test_create_then_edit_lifecycle_reuses_one_comment() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="first")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1 and provider.edits == []
    # A later change edits the same comment in place — never a second post.
    pub.observe(_delta(_row(task="second", tool_calls=9)))
    pub.flush_pending(now=T0 + timedelta(seconds=30))
    assert len(provider.posts) == 1
    assert provider.edits == [("c1", provider.edits[0][1])]
    assert "second" in provider.edits[0][1]


def test_marker_recovery_adopts_the_sticky_comment_not_an_old_reply() -> None:
    """Cold start (daemon restart, no persisted id) on a thread holding BOTH an
    old engine reply (signature marker only) AND the sticky status comment: the
    scan must adopt the STICKY comment and edit it, never the reply — the whole
    reason STICKY_MARKER is distinct from SIGNATURE_MARKER."""
    reply = TicketComment(id="c-reply", body=f"@grove usage help\n\n{SIGNATURE_MARKER}")
    sticky = TicketComment(
        id="c-sticky", body=f"stale status\n\n{STICKY_MARKER}\n{SIGNATURE_MARKER}"
    )
    provider = _FakeProvider(comments=[reply, sticky])  # the reply is FIRST in the thread
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="resumed")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert provider.posts == []  # recovered, not duplicated
    assert provider.edits == [("c-sticky", provider.edits[0][1])]  # the sticky, not the reply


def test_marker_recovery_posts_fresh_when_only_a_reply_exists() -> None:
    """A thread with ONLY an engine reply (signature marker, no sticky marker) has
    no sticky comment to recover: the publisher must POST a new one, never adopt
    the reply and clobber it with the status render."""
    reply = TicketComment(id="c-reply", body=f"@grove usage help\n\n{SIGNATURE_MARKER}")
    provider = _FakeProvider(comments=[reply])
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="fresh")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1  # a new sticky comment, not an edit of the reply
    assert provider.edits == []


def test_kill_publishes_a_terminal_summary_then_stops() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="in-flight")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1
    pub.observe(_kill_delta())  # terminal → immediate final summary on the same comment
    assert len(provider.edits) == 1
    # Publishing has stopped for this workspace — later deltas are ignored.
    pub.observe(_delta(_row(task="ghost", tool_calls=99)))
    pub.flush_pending(now=T0 + timedelta(seconds=60))
    assert len(provider.edits) == 1


def test_provider_failure_is_isolated_and_keeps_the_pipeline_alive() -> None:
    """A forge write that raises is logged + swallowed at the publisher's call
    site (the #193 swallow obligation) — never re-raised into the poll path."""
    provider = _FakeProvider(fail=True)
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="doomed")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))  # must not raise
    # A later, healthy workspace still publishes.
    provider._fail = False
    pub.observe(_delta(_row(task="recovered", tool_calls=5)))
    pub.flush_pending(now=T0 + timedelta(seconds=30))
    assert len(provider.posts) == 1


def test_skips_a_workspace_with_no_ticket_refs() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(refs=())))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert provider.posts == []


def test_skips_when_no_provider_resolves_for_the_ref() -> None:
    pub = _publisher(None, window=5.0)  # resolver always returns None
    pub.observe(_delta(_row(task="orphaned")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))  # no provider → nothing happens


# ─── close() vs timer race ────────────────────────────────────────────────────


def test_run_after_pool_shutdown_degrades_instead_of_raising() -> None:
    """A stale timer thread already past ``close()``'s ``cancel()`` can reach
    ``_run`` after the pool has shut down; ``submit`` then raises ``RuntimeError``
    on the Timer thread. It must degrade to a swallowed drop, never surface."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    # Reproduce the tiny window close() opens: the pool is shut down but a timer
    # thread already holds the reference and calls submit through _run.
    pool = ThreadPoolExecutor(max_workers=1)
    pool.shutdown(wait=True)
    pub._pool = pool
    calls: list[_FlushJob] = []
    pub._run(calls.append, _FlushJob(row=_row(), terminal=False))  # must not raise
    assert calls == []  # submit rejected + swallowed — never fell back to an inline run


# ─── from_config factory ────────────────────────────────────────────────────


def test_from_config_disabled_returns_none() -> None:
    assert TicketStatusPublisher.from_config(IssueOpsConfig(enabled=False), registry=None) is None
