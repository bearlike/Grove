"""TicketStatusPublisher — the live sticky issue-comment.

The publisher copies the ``NotificationBroker``'s discipline (CLAUDE.md): a pure
``render`` over a snapshot dataclass (tested with zero I/O), a coalescing fold
tested against a fake clock, and an end-to-end path driven by hand-built activity
deltas plus a capturing fake provider (the "stub only the I/O boundary" seam every
provider test uses). No threads, no network.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession, TodoItem, TodoList
from grove.core.config import IssueOpsConfig
from grove.core.contracts.issueops import IssueOpsEvent
from grove.core.contracts.phase_palette import DARK_PHASE_HEX
from grove.core.contracts.tickets import TicketComment, TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.issueops import (
    SIGNATURE_MARKER,
    STICKY_MARKER,
    PublishSnapshot,
    TicketStatusPublisher,
)
from grove.core.issueops.publisher import SessionLink, _FlushJob
from grove.core.phase import PhaseReport, TaskPhase, TicketClaim
from grove.core.workspace import CommitSummary, WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 7, 9, 21, 38, 0, tzinfo=UTC)
_GITEA_REF = TicketRef(provider="gitea", id="42")
# The pull request that resolves issue 42. A PR is an ordinary ref on the same
# provider — comment I/O reaches it through the identical issues/{id}/comments
# path — so the publisher needs no PR-specific code, only multi-target routing.
_GITEA_PR_REF = TicketRef(provider="gitea", id="43", kind="pull_request")


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
        agent_kind="claude_code",
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
    phase: PhaseReport | None = None,
    branch: str = "",
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
        phase=phase,
        branch=branch,
    )


def _claim(
    ticket: str, phase: TaskPhase, *, note: str | None = None, blocked: bool = False
) -> TicketClaim:
    """One per-ticket claim, keyed exactly as ``TicketRef.key`` composes it."""
    return TicketClaim(ticket=ticket, phase=phase, note=note, blocked=blocked)


def _report(
    phase: TaskPhase = "implementing",
    *,
    note: str | None = None,
    blocked: bool = False,
    tickets: Sequence[TicketClaim] = (),
) -> PhaseReport:
    """A phase report: the workspace's own claim, plus any per-ticket claims."""
    return PhaseReport(
        phase=phase, note=note, blocked=blocked, updated_at=T0, tickets=tuple(tickets)
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
    """A capturing ``TicketProvider`` — records posts/edits/reads, optionally raises.

    Folds ``can_comment`` from its two halves exactly as ``HttpTicketProvider``
    does — ``comments_supported`` (the tracker backs comment I/O at all; Linear
    does not) AND ``configured`` (a credential is present) — so a routing test
    that trips one half exercises the real predicate's semantics, not a fake's.
    ``fail_ids`` fails writes for named tickets only, so a multi-target test can
    break ONE thread and assert the other survives.

    It also answers the two ENRICHMENT reads and the two repo-link questions,
    because the publisher calls all four at dispatch. A fake that only overrode
    the write half would leave the read half untested while the suite stayed
    green — and the read half is exactly where a bare ref becomes a link. Reads
    are recorded as ``(kind, id)`` pairs so a test can prove *which* namespace a
    ref went to (a PR must never be read as an issue: the issues endpoint calls
    a merged PR ``closed``) and count them for the TTL memo.
    """

    label = "Gitea"

    def __init__(
        self,
        *,
        name: TicketProviderName = "gitea",
        comments: Sequence[TicketComment] = (),
        fail: bool = False,
        fail_ids: Sequence[str] = (),
        configured: bool = True,
        comments_supported: bool = True,
        statuses: dict[str, str] | None = None,
        enrich_fails: bool = False,
        links: bool = True,
        body_supported: bool = True,
        bodies: dict[str, str] | None = None,
    ) -> None:
        self.name: TicketProviderName = name
        self.configured = configured
        self.comments_supported = comments_supported
        self.body_supported = body_supported
        # Real per-ticket body state, not a scripted answer: the footer splice is
        # read-modify-write, so a double whose write does not move its own next
        # read would let an unsplicing (or duplicating) implementation pass.
        self.bodies: dict[str, str] = dict(bodies or {})
        self.body_writes: list[tuple[str, str]] = []
        self.posts: list[tuple[str, str]] = []
        self.edits: list[tuple[str, str]] = []
        self.listed: list[str] = []
        self.reads: list[tuple[str, str]] = []  # (kind, ticket id) per enrichment call
        self._comments = list(comments)
        self._fail = fail
        self._fail_ids = set(fail_ids)
        self._statuses = statuses or {}
        self._enrich_fails = enrich_fails
        self._links = links
        self.base_url = f"https://forge.example.com/{name}/proj"
        self._ids = itertools.count(1)
        self._owner: dict[str, str] = {}  # comment id → the ticket it lives on

    @property
    def can_comment(self) -> bool:
        return self.comments_supported and self.configured

    @property
    def can_edit_body(self) -> bool:
        return self.body_supported and self.configured

    @property
    def web_root(self) -> str | None:
        return "https://forge.example.com" if self._links else None

    def read_body(self, ticket_id: str) -> str:
        return self.bodies.get(ticket_id, "")

    def update_body(self, ticket_id: str, body: str) -> None:
        if self._fail or ticket_id in self._fail_ids:
            raise TicketProviderError(f"{self.name} body write refused for {ticket_id}")
        self.bodies[ticket_id] = body
        self.body_writes.append((ticket_id, body))

    # ── enrichment reads (resolved at dispatch, never persisted on the ref) ──

    def get_ticket(self, ticket_id: str) -> TicketRef:
        return self._enriched("issue", ticket_id, f"{self.base_url}/issues/{ticket_id}")

    def get_pull_request(self, ticket_id: str) -> TicketRef:
        return self._enriched("pull_request", ticket_id, f"{self.base_url}/pulls/{ticket_id}")

    def _enriched(self, kind: str, ticket_id: str, url: str) -> TicketRef:
        self.reads.append((kind, ticket_id))
        if self._enrich_fails:
            raise TicketProviderError("boom")
        return TicketRef(
            provider=self.name,
            id=ticket_id,
            kind=kind,  # type: ignore[arg-type]
            title=f"ticket {ticket_id}",
            url=url,
            status=self._statuses.get(ticket_id, "open"),
        )

    # ── repo-level links (None where the tracker fronts no repo) ────────────

    @property
    def context(self) -> str | None:
        return "acme/proj" if self._links else None

    def branch_url(self, branch: str) -> str | None:
        return f"{self.base_url}/src/branch/{branch}" if self._links else None

    def commit_url(self, sha: str) -> str | None:
        return f"{self.base_url}/commit/{sha}" if self._links else None

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        self.listed.append(ticket_id)
        return [c for c in self._comments if self._owner.get(c.id, ticket_id) == ticket_id]

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        if self._fail or ticket_id in self._fail_ids:
            raise TicketProviderError("boom")
        comment = TicketComment(id=f"c{next(self._ids)}", body=body)
        self._comments.append(comment)
        self._owner[comment.id] = ticket_id
        self.posts.append((ticket_id, body))
        return comment

    def edit_comment(self, comment_id: str, body: str) -> None:
        if self._fail or self._owner.get(comment_id, "") in self._fail_ids:
            raise TicketProviderError("boom")
        self.edits.append((comment_id, body))


def _publisher(
    provider: _FakeProvider | None,
    *,
    providers: Sequence[_FakeProvider] = (),
    todo: TodoList | None = None,
    phase: PhaseReport | None = None,
    phase_resolver: Callable[[str, str], PhaseReport | None] | None = None,
    task_resolver: Callable[[str, str], str | None] | None = None,
    transcript_probe: Callable[[str, str, str], bool] | None = None,
    ticket_held: bool | None = None,
    window: float = 5.0,
    clock: object | None = None,
    terminal_states: frozenset[AgentActivityState] = frozenset(),
) -> TicketStatusPublisher:
    table: dict[str, _FakeProvider] = {p.name: p for p in providers}
    if provider is not None:
        table.setdefault(provider.name, provider)
    return TicketStatusPublisher(
        config=IssueOpsConfig(
            enabled=True,
            update_window_seconds=window,
            deep_link_base_url="https://grove.example.com",
        ),
        provider_resolver=lambda root, name: table.get(name),
        todo_resolver=lambda root, ws_id: todo,
        phase_resolver=phase_resolver or (lambda root, ws_id: phase),
        task_resolver=task_resolver,
        transcript_probe=transcript_probe,
        holder_resolver=(None if ticket_held is None else (lambda root, refs: ticket_held)),
        clock=clock or (lambda: T0),
        terminal_states=terminal_states,
    )


# ─── pure render (zero I/O) ────────────────────────────────────────────────────


def _snapshot(**kw: object) -> PublishSnapshot:
    base: dict[str, object] = {
        "workspace_id": "ws1",
        "title": "fix-auth",
        "repo_label": "acme/proj",
        "branch": "grove/42-fix-auth",
        "agent_name": "claude",
        "agent_kind": "claude_code",
        "state": AgentActivityState.WORKING,
        "current_task": None,
        "todo": None,
        "phase": None,
        "latest_commit": None,
        "deep_link": "https://grove.example.com/w/ws1",
        "terminal": False,
        "occurred_at": T0,
        "sessions": (),
        "ticket_held": None,
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


def test_the_checklist_collapses_under_a_fixed_title_and_a_table_row() -> None:
    """The list grows without bound as the task proceeds, so it collapses — but
    its progress is NOT in the title any more. A title carrying a count changes
    between renders and so cannot be recognized at a glance, which is the whole
    job of a heading in a comment a reader has seen on ten other tickets. The
    count is the summary table's ``Checklist`` row instead, so the overview
    stays visible while the items sit one click away. ``in_progress`` is not
    "done" — a forge task list has two states only."""
    todo = TodoList(
        items=(
            TodoItem(content="write the parser", status="completed"),
            TodoItem(content="wire the daemon", status="in_progress"),
            TodoItem(content="add docs", status="pending"),
        )
    )
    body = TicketStatusPublisher.render(_snapshot(todo=todo))
    assert "<summary>☑️ Checklist</summary>" in body
    assert "| ☑️ **Checklist** | 1 of 3 done |" in body
    # Unbounded content collapses: the checklist is a plain <details>, never open.
    assert "<details>\n<summary>☑️ Checklist</summary>" in body


def test_the_activity_block_strips_the_agents_own_harness_markup() -> None:
    """``current_task`` is a transcript excerpt, so it carries whatever markup
    the agent's harness speaks. Both forges' sanitizers drop an unknown tag
    anyway — leaving it in only makes the stored body disagree with what a
    reader sees, and an unbalanced ``<`` swallows the text after it."""
    task = '<teammate-message teammate_id="lead">rebase onto main</teammate-message>'
    body = TicketStatusPublisher.render(_snapshot(current_task=task))
    assert "teammate-message" not in body
    # The title no longer previews the text: it was the worst of both, giving a
    # reader who wanted the gist a fragment and a reader who wanted the text a
    # fold to expand anyway. The whole text lives in the body.
    assert "<summary>💬 Latest activity</summary>" in body
    assert "rebase onto main" in body


_SECTION_TITLES = ["🧭 Progress", "💬 Latest activity", "☑️ Checklist", "🔗 Tracking"]


def test_the_section_titles_are_fixed_and_reproducible() -> None:
    """The property most likely to be broken by a future "helpful" edit.

    Four titles, always the same words in the same order, so a reader learns the
    comment's shape ONCE and recognizes it across every ticket a fleet touches.
    That is only true while no count, state or timestamp is allowed into a
    title — the moment one is, the heading changes between renders and cannot be
    recognized at a glance. Every varying number lives in the summary table
    instead, which is why the table exists.

    Asserted against a render carrying content in all four sections, so a title
    that quietly re-acquires its old count breaks here rather than in whichever
    section-specific test happens to look at it."""
    todo = TodoList(items=(TodoItem(content="write the parser", status="completed"),))
    body = TicketStatusPublisher.render(
        _snapshot(
            todo=todo,
            current_task="rebasing onto main",
            phase=PhaseReport(phase="verifying", note="running make lint", updated_at=T0),
            tickets=(TicketRef(provider="gitea", id="42", url="https://g/i/42", status="open"),),
        )
    )
    titles = [
        line.removeprefix("<summary>").removesuffix("</summary>")
        for line in body.splitlines()
        if line.startswith("<summary>")
    ]
    assert titles == _SECTION_TITLES


def test_bounded_sections_stay_open_and_unbounded_ones_collapse() -> None:
    """The fold is a statement about GROWTH, not importance.

    Progress is six nodes and Tracking is the refs a workspace names — both
    bounded, both visible, and Tracking is the one thing a reader of *this*
    thread cannot get anywhere else. The agent's latest activity and the
    checklist grow for as long as the task runs, so they collapse behind their
    (fixed) titles with the numbers a scanner wanted left in the table."""
    todo = TodoList(items=(TodoItem(content="write the parser", status="completed"),))
    body = TicketStatusPublisher.render(
        _snapshot(
            todo=todo,
            current_task="rebasing onto main",
            phase=PhaseReport(phase="verifying", note=None, updated_at=T0),
            tickets=(TicketRef(provider="gitea", id="42"),),
        )
    )
    for title in ("🧭 Progress", "🔗 Tracking"):
        assert f"<details open>\n<summary>{title}</summary>" in body
    for title in ("💬 Latest activity", "☑️ Checklist"):
        assert f"<details>\n<summary>{title}</summary>" in body


def test_the_activity_body_is_a_timestamped_hard_wrapped_fenced_block() -> None:
    """Three jobs, one fence. A fenced block makes agent-written text inert, so a
    stray ``#`` or ``|`` in it cannot become markup; the text is hard-wrapped
    because a code block scrolls sideways rather than reflowing, and dragging a
    scrollbar to read one sentence is not reading; and the ``[updated …]`` line
    gives the excerpt back the timestamp it loses by sitting behind a fold.

    Wrapping bounds the LINES, never the text — nothing is cut."""
    text = "wire the parser " * 40  # far past any single line
    body = TicketStatusPublisher.render(_snapshot(current_task=text))
    block = body.partition("```\n")[2].partition("\n```")[0]
    lines = block.splitlines()
    assert lines[0] == "[updated 2026-07-09 21:38 UTC]"
    assert lines[1] == ""
    assert all(len(line) <= 88 for line in lines)
    assert " ".join(lines[2:]) == text.strip()


def test_a_fenced_block_in_the_task_text_cannot_break_out_of_the_activity_block() -> None:
    """Value-becomes-syntax, the same class as the mermaid escaping and the
    table-cell pipe. Agent text routinely contains a fenced block of its own, and
    a three-backtick fence would then be CLOSED by the agent's own content —
    everything after it escaping into the comment as live markup. The fence
    lengthens past the longest backtick run in the text instead."""
    text = "here is the fix:\n```python\nprint('hi')\n```\ndone"
    body = TicketStatusPublisher.render(_snapshot(current_task=text))
    section = body.partition("<summary>💬 Latest activity</summary>")[2]
    block, _, after = section.partition("\n</details>")
    fence = next(line for line in block.splitlines() if line.startswith("`"))
    assert len(fence) > 3  # lengthened past the agent's own ```
    assert block.count(fence) == 2  # opened and closed exactly once
    assert "print('hi')" in block  # the agent's code stayed INSIDE the block
    assert "print('hi')" not in after


def test_no_activity_block_without_task_text() -> None:
    body = TicketStatusPublisher.render(_snapshot(current_task=None))
    assert "Latest activity" not in body


def test_render_includes_branch_commit_task_and_deep_link() -> None:
    commit = CommitSummary(sha="abc1234", subject="init parser", committed_at=T0)
    body = TicketStatusPublisher.render(
        _snapshot(current_task="parsing the branch", latest_commit=commit)
    )
    assert body.startswith("## 🔨 Grove — Working\n")
    assert "| 🌿 **Branch** | `grove/42-fix-auth` (acme/proj) |" in body
    assert "| 📌 **Commit** | `abc1234` — init parser |" in body
    assert "| 🖥️ **Workspace** | [Open in Grove](https://grove.example.com/w/ws1) |" in body
    assert "| 🕒 **Updated** | 2026-07-09 21:38 UTC |" in body
    assert "parsing the branch" in body


def test_the_live_body_states_its_state_once_and_the_terminal_body_states_it_as_a_row() -> None:
    """The loudest signal in the comment is stated exactly once, and WHERE it is
    stated is what decides whether a row exists.

    The live heading already names the state, so a ``Status`` row would render
    the same fact twice in the reader's first two lines. The terminal heading
    says "Session ended" instead — nothing else in that body names the state the
    agent finished in, so the row that the live body must not have is precisely
    the row the terminal body must."""
    live = TicketStatusPublisher.render(_snapshot())
    assert live.startswith("## 🔨 Grove — Working\n")
    assert "**Status**" not in live
    assert "**Final state**" not in live

    terminal = TicketStatusPublisher.render(_snapshot(terminal=True))
    assert terminal.startswith("## ✅ Grove — Session ended\n")
    assert "| 🚦 **Final state** | 🔨 Working |" in terminal


def test_render_omits_deep_link_when_absent() -> None:
    body = TicketStatusPublisher.render(_snapshot(deep_link=None))
    assert "http" not in body
    assert "Workspace" not in body


def test_a_summary_row_exists_only_for_a_fact_that_exists() -> None:
    """ "Absence is not a state" is a table rule too: an empty Phase or Commit
    cell would assert the fact is known and blank, where no row at all says the
    honest thing — the publisher has nothing to report on that axis."""
    body = TicketStatusPublisher.render(_snapshot())
    assert "**Phase**" not in body
    assert "**Commit**" not in body
    assert "**Checklist**" not in body
    # The rows that always exist still do, so the absence above is selective.
    assert "| 🌿 **Branch** |" in body and "| 🕒 **Updated** |" in body


def test_the_branch_and_commit_link_only_where_a_destination_exists() -> None:
    """A link is built only where it lands somewhere. The bare form is not a
    degraded link — it is the correct render for a tracker that fronts no repo
    (or an unscoped one), because a link that goes nowhere spends the reader's
    click to tell them nothing."""
    commit = CommitSummary(sha="abc1234", subject="init parser", committed_at=T0)
    linked = TicketStatusPublisher.render(
        _snapshot(
            latest_commit=commit,
            branch_url="https://g/b/fix-auth",
            commit_url="https://g/c/abc1234",
        )
    )
    assert "| 🌿 **Branch** | [`grove/42-fix-auth`](https://g/b/fix-auth) (acme/proj) |" in linked
    assert "| 📌 **Commit** | [`abc1234`](https://g/c/abc1234) — init parser |" in linked

    plain = TicketStatusPublisher.render(_snapshot(latest_commit=commit))
    assert "| 🌿 **Branch** | `grove/42-fix-auth` (acme/proj) |" in plain
    assert "| 📌 **Commit** | `abc1234` — init parser |" in plain


def test_the_branch_row_names_its_repository() -> None:
    """A branch name alone is ambiguous the moment a reader is looking at more
    than one project — which is what reading this comment on a TRACKER is. The
    link already encodes the repository; only a reader who hovers can see it."""
    body = TicketStatusPublisher.render(_snapshot(branch_url="https://g/b/fix-auth"))
    assert "| 🌿 **Branch** | [`grove/42-fix-auth`](https://g/b/fix-auth) (acme/proj) |" in body
    # Nothing to name is no bracket, not an empty one.
    bare = TicketStatusPublisher.render(_snapshot(repo_label=""))
    assert "| 🌿 **Branch** | `grove/42-fix-auth` |" in bare


def test_the_agent_row_names_the_kind_and_the_profile_but_never_the_model() -> None:
    """Who did the work, documentary and stable for the workspace's whole life.

    The model identifier is deliberately absent: it dates the comment and invites
    conclusions the comment cannot support. The profile renders only when it says
    something the kind's label does not — a profile named ``codex`` would
    otherwise read "Codex (codex)"."""
    body = TicketStatusPublisher.render(_snapshot())
    assert "| 🤖 **Agent** | Claude Code (claude) |" in body

    same = TicketStatusPublisher.render(_snapshot(agent_name="Codex", agent_kind="codex"))
    assert "| 🤖 **Agent** | Codex |" in same


def test_an_unknown_agent_renders_no_row_rather_than_the_word_unknown() -> None:
    """The same "absence is not a state" rule the phase axis follows. A record
    written before ``agent_kind`` existed still has its profile name, which is a
    fact worth showing on its own; neither known is no row at all."""
    profile_only = TicketStatusPublisher.render(_snapshot(agent_kind=None))
    assert "| 🤖 **Agent** | claude |" in profile_only

    neither = TicketStatusPublisher.render(_snapshot(agent_name="", agent_kind=None))
    assert "**Agent**" not in neither


def test_dispatch_carries_the_agent_and_the_repository_into_the_render() -> None:
    """Both are persisted on the workspace at create — ahead of any activity row
    — which is exactly why neither needs a place in ``_render_fingerprint``: a
    constant that is absent at the first render would never appear at all."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="mirrored")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.posts[0][1]
    assert "| 🤖 **Agent** | Claude Code (claude) |" in body
    assert "(acme/proj) |" in body


def test_a_pipe_in_a_commit_subject_does_not_shear_the_table_row() -> None:
    """A commit subject is free text entering table syntax — the same
    value-becomes-syntax class as the mermaid note, fixed where the value
    enters. An unescaped ``|`` ends the cell early and the row renders as
    columns nobody wrote."""
    commit = CommitSummary(sha="abc1234", subject="fix a|b parsing\nand more", committed_at=T0)
    body = TicketStatusPublisher.render(_snapshot(latest_commit=commit))
    row = next(line for line in body.splitlines() if "**Commit**" in line)
    assert row == "| 📌 **Commit** | `abc1234` — fix a\\|b parsing and more |"
    assert row.count("|") - row.count("\\|") == 3  # the row's own three delimiters


def _diagram(body: str) -> str:
    """The mermaid fence's contents, or ``""`` when the body carries no diagram."""
    _, sep, rest = body.partition("```mermaid\n")
    return rest.partition("```")[0] if sep else ""


def test_render_draws_a_horizontal_flowchart_of_every_phase() -> None:
    """The status report: six nodes in ``PHASE_ORDER``, left to right."""
    report = PhaseReport(phase="verifying", note=None, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    assert chart.startswith("flowchart LR")
    for label in ("Scoping", "Planning", "Implementing", "Verifying", "Delivering", "Done"):
        assert f'"{label}"' in chart
    assert "p0 --> p1 --> p2 --> p3 --> p4 --> p5" in chart


def test_render_colours_done_current_and_remaining_from_the_phase_palette() -> None:
    """Completed / current / remaining each take the palette member that already
    means it — no invented hexes, so a block reads like the TUI and webapp badge."""
    report = PhaseReport(phase="implementing", note=None, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    assert f"classDef done fill:{DARK_PHASE_HEX['done']}" in chart
    assert f"classDef now fill:{DARK_PHASE_HEX['implementing']}" in chart
    assert f"classDef todo fill:{DARK_PHASE_HEX['scoping']}" in chart
    # The phases before the current one are done, the current one is now, the rest remain.
    assert '"Scoping"]:::done' in chart and '"Planning"]:::done' in chart
    assert '"Implementing"]:::now' in chart
    assert '"Verifying"]:::todo' in chart and '"Done"]:::todo' in chart


def test_every_diagram_node_carries_an_explicit_fill_and_label_colour() -> None:
    """What makes the chart theme-independent: an unfilled node would inherit the
    forge's page background (white in light mode, near-black in dark) and no one
    label colour could stay legible on both."""
    report = PhaseReport(phase="scoping", note=None, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    class_defs = [line for line in chart.splitlines() if line.strip().startswith("classDef")]
    assert len(class_defs) == 3
    assert all("fill:#" in line and "color:#" in line for line in class_defs)


def test_the_current_phase_stays_distinct_where_its_fill_collides() -> None:
    """``scoping`` current and ``scoping`` remaining share a fill by construction
    (the ramp's palest anchor means both "not yet producing" and "not started").
    A thick dark ring — not a fourth colour — is what separates the current node
    there, and it survives a reader who cannot tell the hues apart at all."""
    for phase in ("scoping", "done"):
        report = PhaseReport(phase=phase, note=None, updated_at=T0)  # type: ignore[arg-type]
        chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
        now_def = next(line for line in chart.splitlines() if "classDef now" in line)
        assert "stroke-width:3px" in now_def


def test_the_current_phase_keeps_a_colour_of_its_own_at_the_final_phase() -> None:
    """The one collision NOT left to the ring, and the reason is where a
    workspace stops: the last render is the one a reader meets forever after, so
    a current node wearing the completed gray leaves that comment saying nothing
    about where the work ended except through a ring.

    The fill is still a palette member (the ramp's deepest live entry), the ink
    does not move with it — white would reach only 3.4:1 there, against 5.6:1
    for the one dark ink — and the ring stays on top."""
    report = PhaseReport(phase="done", note=None, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    now_def = next(line for line in chart.splitlines() if "classDef now" in line)
    done_def = next(line for line in chart.splitlines() if "classDef done" in line)

    assert f"fill:{DARK_PHASE_HEX['delivering']}" in now_def
    assert f"fill:{DARK_PHASE_HEX['done']}" in done_def
    assert "stroke-width:3px" in now_def  # the ring survives the new fill
    assert now_def.endswith("color:#111111")  # one dark ink, no per-class exception
    assert '"Done"]:::now' in chart


def test_the_progress_section_is_never_collapsed() -> None:
    """The phase is the one thing a reader wants without a click, so Progress is
    open in EVERY body — live and terminal alike. Asserted on its own rather
    than only inside the bounded/unbounded test, because the regression this
    guards against is a one-character edit in a shared helper."""
    report = PhaseReport(phase="verifying", note="running make lint", updated_at=T0)
    for terminal in (False, True):
        body = TicketStatusPublisher.render(_snapshot(phase=report, terminal=terminal))
        assert "<details open>\n<summary>🧭 Progress</summary>" in body


def test_render_puts_the_agents_note_on_the_current_node() -> None:
    report = PhaseReport(phase="verifying", note="running make lint", updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    assert '"Verifying<br>running make lint"]:::now' in chart


def test_render_escapes_a_note_that_would_otherwise_break_the_diagram() -> None:
    """A single ``"`` in an agent-written note does not degrade the chart, it
    replaces the whole thing with a mermaid parse error (reproduced on a real
    Gitea render). Every breaking character leaves the label as an entity code,
    and the raw glyph never survives into the mermaid source."""
    note = 'say "hi" [a] (b) {c} <d> #e | f `g`\nsecond line'
    report = PhaseReport(phase="planning", note=note, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    node = next(line for line in chart.splitlines() if ":::now" in line)
    # Only the agent's note is escaped; the ``<br>`` Grove itself emits is markup.
    assert "<br>" in node
    escaped_note = node.partition("<br>")[2].rpartition('"]')[0]
    assert not set(escaped_note) & set('"[](){}<>|`\n')
    assert "#quot;" in escaped_note and "#91;" in escaped_note and "#35;e" in escaped_note


def test_a_long_note_is_truncated_before_it_is_escaped() -> None:
    """Truncating afterwards could slice an entity code in half and leave a
    literal ``#12`` on screen; it also bounds the widest node, which sets the
    whole horizontal chart's width."""
    report = PhaseReport(phase="planning", note='"' * 200, updated_at=T0)
    chart = _diagram(TicketStatusPublisher.render(_snapshot(phase=report)))
    node = next(line for line in chart.splitlines() if ":::now" in line)
    label = node.partition("<br>")[2].rpartition('"]')[0]
    assert label.count("#quot;") == 47  # the cap, minus one for the ellipsis
    assert label.endswith("…")


def test_a_workspace_with_no_reported_phase_draws_no_diagram_at_all() -> None:
    """Absence is not a state. An all-remaining chart would assert "nothing is
    done yet", which Grove cannot know — the agent may be nearly finished and
    simply not reporting. So a non-reporting workspace's comment is exactly what
    it was before this axis existed: no diagram, no caption, no placeholder."""
    body = TicketStatusPublisher.render(_snapshot(phase=None))
    assert "```mermaid" not in body
    assert "flowchart" not in body
    assert "**Phase**" not in body
    # Not an empty section either — the whole Progress block is absent.
    assert "Progress" not in body


def test_the_plain_text_phase_caption_rides_beside_the_diagram() -> None:
    """The fallback: a surface that renders no mermaid is no worse off than
    before, and a screen reader gets the answer in one line.

    It is the summary table's Phase row, so the row label supplies the word
    "Phase" — the caption itself must not repeat it, or the fact appears twice
    in one line. The caption is dots + name + position and NOTHING else: the
    agent's note is prose that can run to 200 characters, and a table cell that
    long wrecks the column, so the note renders under the diagram instead."""
    note = "x" * 120
    report = PhaseReport(phase="verifying", note=note, updated_at=T0)
    body = TicketStatusPublisher.render(_snapshot(phase=report))
    assert "```mermaid" in body
    assert "| 🧭 **Phase** | ●●●●○○ Verifying · 4 of 6 |" in body


def test_the_agents_note_renders_exactly_once_as_a_blockquote_under_the_diagram() -> None:
    """The note is prose, and prose has one home.

    It is capped at 48 characters inside the mermaid node (a horizontal chart is
    as wide as its widest node), so the FULL note has to appear somewhere — as a
    blockquote in Progress, where there is room for it. The table's Phase row
    deliberately does not carry it, and counting occurrences is what pins that:
    a "helpful" future edit re-adding it to the row would leave every render
    saying the same sentence twice, which no single-``in`` assertion can see."""
    note = "the parser rewrite is nearly through its second pass, " + "x" * 120
    report = PhaseReport(phase="verifying", note=note, updated_at=T0)
    body = TicketStatusPublisher.render(_snapshot(phase=report))
    # Uncut, and exactly once — the diagram's copy is capped, so the blockquote
    # is the only place the whole note ever appears.
    assert body.count(note) == 1
    assert f"> {note}" in body
    # And it sits inside Progress, not loose in the body.
    progress = body.partition("<summary>🧭 Progress</summary>")[2].partition("</details>")[0]
    assert f"> {note}" in progress


# ─── the phase is PER TICKET (the one thing a body does not share) ───────────


def test_each_target_renders_its_own_tickets_claim() -> None:
    """The premise of the whole per-ticket axis, at the render seam.

    One workspace routinely carries an issue and the pull request that closes
    it, and one shared phase cannot say the issue is delivering while the PR is
    blocked on a review. The body is otherwise identical on both threads — the
    agent state, the branch, the checklist all answer for the WORKSPACE — so
    ``focus`` is the one input that makes two bodies out of one snapshot."""
    report = _report(
        "scoping",  # the workspace's own claim: deliberately unlike either ticket's
        tickets=(
            _claim("gitea:42", "delivering"),
            _claim("gitea:43", "verifying", blocked=True, note="needs a review decision"),
        ),
    )
    issue = TicketStatusPublisher.render(_snapshot(phase=report), focus="gitea:42")
    pull = TicketStatusPublisher.render(_snapshot(phase=report), focus="gitea:43")

    assert "| 🧭 **Phase** | ●●●●●○ Delivering · 5 of 6 |" in issue
    assert "| 🧭 **Phase** | ●●●●○○ ⛔ Verifying, blocked · 4 of 6 |" in pull
    assert issue != pull
    # Neither thread inherits the workspace's own claim while it has one of its own.
    assert "Scoping · 1 of 6" not in issue
    assert "Scoping · 1 of 6" not in pull


def test_a_ticket_with_no_claim_of_its_own_inherits_the_workspaces() -> None:
    """The fallback, and it is a deliberate direction rather than a default.

    An agent reporting one phase for a job that happens to name three tickets is
    the ordinary case; withholding that claim from the two it did not name
    individually would make per-ticket reporting a DOWNGRADE for everyone who
    never opts in."""
    report = _report(
        "implementing", note="wiring the parser", tickets=(_claim("gitea:42", "delivering"),)
    )
    body = TicketStatusPublisher.render(_snapshot(phase=report), focus="gitea:99")
    assert "| 🧭 **Phase** | ●●●○○○ Implementing · 3 of 6 |" in body
    assert "> wiring the parser" in body
    # And with no focus at all — every non-ticket caller — it is the same claim.
    assert TicketStatusPublisher.render(_snapshot(phase=report)) == body


def test_a_ticket_nobody_reported_on_reads_as_unreported_never_as_scoping() -> None:
    """The distinction the whole axis rests on, applied per ticket.

    "Has not reported" is a fleet-health fact about the AGENT; "is scoping" is
    progress on the task. Defaulting the first to the second would destroy the
    difference on the one surface a human triages from — and it would do it
    silently, because ``scoping`` renders perfectly well. Pinned as a PAIR, so
    the assertion can only pass while the two really do render differently."""
    unreported = TicketStatusPublisher.render(_snapshot(phase=None), focus="gitea:42")
    assert "**Phase**" not in unreported
    assert "```mermaid" not in unreported
    assert "Progress" not in unreported
    assert "Scoping" not in unreported

    seeded = TicketStatusPublisher.render(
        _snapshot(phase=_report("scoping", tickets=(_claim("gitea:42", "scoping"),))),
        focus="gitea:42",
    )
    assert "| 🧭 **Phase** | ●○○○○○ Scoping · 1 of 6 |" in seeded


def test_blocked_renders_beside_the_phase_and_never_instead_of_it() -> None:
    """A flag orthogonal to the position, so both facts have to survive.

    ``scoping, blocked`` is a ticket nobody can even start; ``verifying,
    blocked`` is work that is substantially done and wants one decision. A
    render that replaced the phase name with the word "blocked" would throw away
    the more actionable half AND leave the "4 of 6" beside it contradicted. The
    reason rides the note, which is the one place with room to print it whole."""
    report = _report("verifying", note="the API contract is ambiguous", blocked=True)
    body = TicketStatusPublisher.render(_snapshot(phase=report))

    assert "| 🧭 **Phase** | ●●●●○○ ⛔ Verifying, blocked · 4 of 6 |" in body
    assert '"⛔ Verifying<br>the API contract is ambiguous"]:::now' in _diagram(body)
    assert "> ⛔ **Blocked** — the API contract is ambiguous" in body

    # Blocked with nothing to say still says it — the flag is the signal, the
    # note is the explanation, and a missing explanation must not hide the flag.
    silent = TicketStatusPublisher.render(_snapshot(phase=_report("scoping", blocked=True)))
    assert "> ⛔ **Blocked**" in silent
    assert "●○○○○○ ⛔ Scoping, blocked · 1 of 6" in silent


def test_blocked_does_not_repaint_the_diagram() -> None:
    """The chart's colours encode ONE thing — where on the ramp this is. Blocked
    is orthogonal to that, so it takes the glyph the comment already uses for
    the concept and leaves every fill alone; a colour would say "this phase"
    where the fact is "this phase, stuck"."""
    plain = _diagram(TicketStatusPublisher.render(_snapshot(phase=_report("verifying"))))
    stuck = _diagram(
        TicketStatusPublisher.render(_snapshot(phase=_report("verifying", blocked=True)))
    )
    assert _class_defs(plain) == _class_defs(stuck)
    assert '"Verifying"]:::now' in plain and '"⛔ Verifying"]:::now' in stuck


def _class_defs(chart: str) -> list[str]:
    return [line for line in chart.splitlines() if line.strip().startswith("classDef")]


def test_dispatch_gives_each_thread_the_phase_of_the_ticket_it_lands_on() -> None:
    """The render seam, driven the way production drives it: one snapshot, one
    todo/phase/enrichment resolution, and one render per target keyed on the
    ref's own ``TicketRef.key``. A second spelling of that key anywhere would
    pick no claim at all and read as an agent that never reported."""
    provider = _FakeProvider()
    report = _report(
        "implementing",
        tickets=(
            _claim("gitea:42", "verifying"),
            _claim("gitea:43", "delivering", blocked=True),
        ),
    )
    pub = _publisher(provider, window=5.0, phase=report)
    pub.observe(_delta(_row(task="mirrored", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))

    bodies = dict(provider.posts)
    assert "●●●●○○ Verifying · 4 of 6" in bodies["42"]
    assert "●●●●●○ ⛔ Delivering, blocked · 5 of 6" in bodies["43"]
    assert bodies["42"] != bodies["43"]
    # Everything that answers for the WORKSPACE still reads the same on both.
    for shared in ("| 🌿 **Branch** |", "| 🤖 **Agent** |", "- ticket 42 — #42"):
        assert shared in bodies["42"] and shared in bodies["43"]


# ─── the tracking block (issues, pull requests, the way back to Grove) ────────


def test_render_names_every_associated_issue_and_pull_request() -> None:
    """The cross-link the multi-target design deferred: one body on every thread,
    so naming all the refs is what lets a reader on the issue find the PR that
    resolves it, and vice versa. Issues sort ahead of pull requests.

    Each entry is a prose line ENDING in its reference — a ticket's own title
    says what it is better than a ``Kind`` column could, and the sentence puts
    the click target where the eye finishes."""
    tickets = (
        TicketRef(
            provider="gitea",
            id="43",
            kind="pull_request",
            title="Bound the query payload",
            url="https://g/p/43",
            status="merged",
        ),
        TicketRef(
            provider="gitea",
            id="42",
            kind="issue",
            title="Validate the submit boundary",
            url="https://g/i/42",
            status="open",
        ),
    )
    body = TicketStatusPublisher.render(_snapshot(tickets=tickets))
    assert "<summary>🔗 Tracking</summary>" in body
    assert "- Validate the submit boundary — #42" in body
    assert "- Bound the query payload — #43" in body
    assert body.index("— #42") < body.index("— #43")


def test_a_tracked_reference_is_written_bare_so_the_forge_links_it_itself() -> None:
    """The mechanism behind the prose form, and the thing most likely to be
    "fixed" back into a markdown link by a future reader who sees a ``url`` on
    the ref and no link in the render.

    A forge turns a bare ``#42`` into a live reference to its own thread;
    ``[#42](url)`` renders as ordinary link text instead. Verified on this
    Gitea's own renderer, including inside a ``<details>`` body — which is
    where every one of these sits."""
    ref = TicketRef(provider="gitea", id="42", title="Bound it", url="https://g/i/42")
    body = TicketStatusPublisher.render(_snapshot(tickets=(ref,)))
    assert "- Bound it — #42" in body
    assert "[#42]" not in body


def test_the_tracking_entries_carry_neither_kind_nor_status() -> None:
    """A signal deliberately GIVEN UP, not delegated — and the distinction is
    the whole reason this test exists.

    It would be comfortable to believe the forge renders a reference's live
    state for us. Measured, it does not: an open issue, a closed issue and a
    merged pull request all render with the identical ``ref-issue`` class and
    colour, no strikethrough and no tooltip. What this drops is dropped for
    every reader. The trade: this comment says where the WORKSPACE is, a status
    rendered here is only as fresh as the last flush, and the ticket's own page
    is one click away and never stale."""
    merged = TicketRef(provider="gitea", id="43", kind="pull_request", status="merged")
    closed = TicketRef(provider="gitea", id="44", kind="pull_request", status="closed")
    body = TicketStatusPublisher.render(_snapshot(tickets=(merged, closed)))
    tracking = body.partition("<summary>🔗 Tracking</summary>")[2].partition("</details>")[0]
    assert [line for line in tracking.splitlines() if line.strip()] == ["- #43", "- #44"]
    for absent in ("merged", "closed", "Pull request", "Issue"):
        assert absent not in tracking


def test_the_tracking_block_renders_only_what_exists() -> None:
    """An unenriched ref is its bare reference alone — no dash, no empty title —
    and no refs at all is no block."""
    bare = TicketRef(provider="gitea", id="42")
    body = TicketStatusPublisher.render(_snapshot(tickets=(bare,), deep_link=None))
    assert "- #42" in body
    assert "Tracking" not in TicketStatusPublisher.render(_snapshot(tickets=(), deep_link=None))


def test_the_terminal_summary_keeps_the_diagram_and_the_tracking_block() -> None:
    """A reader landing on the final comment still wants the shape of the work and
    the links out of it."""
    report = PhaseReport(phase="delivering", note=None, updated_at=T0)
    tickets = (TicketRef(provider="gitea", id="42", kind="issue", url="https://g/i/42"),)
    body = TicketStatusPublisher.render(_snapshot(terminal=True, phase=report, tickets=tickets))
    assert "```mermaid" in body
    assert "- #42" in body
    assert "| 🕒 **Concluded** | 2026-07-09 21:38 UTC |" in body


def test_dispatch_carries_the_workspaces_ticket_refs_into_the_render() -> None:
    """The snapshot is built from the same refs routing turns into targets, so the
    tracking block can't drift from where the comment is actually posted."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="mirrored", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.posts[0][1]
    assert "- ticket 42 — #42" in body
    assert "- ticket 43 — #43" in body


# ─── the badge footer on the ticket's own description ───────────────────────


def test_publishing_upserts_the_badge_footer_into_the_description() -> None:
    """A reader meets the DESCRIPTION first; the status comment may be a long scroll away."""
    provider = _FakeProvider()
    provider.bodies["42"] = "the human's description"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.bodies["42"]
    assert "the human's description" in body  # never damaged
    assert "open_in-grove_workspace-grey.svg" in body
    assert "scroll_to-grove_status-grey.svg" in body
    assert "#issuecomment-" in body  # the status badge reaches the comment itself


def test_the_status_badge_points_at_the_path_the_reader_is_already_on() -> None:
    """A PR anchor must use the pulls path, or clicking it navigates rather than scrolls.

    A forge serves the ISSUE path for a pull request by 303-redirecting to the
    pulls path, so a PR footer built on `/issues/<n>` sends the reader on a
    round trip back to the page they were already looking at. Comment I/O is
    correctly kind-blind; a link cannot be.

    The tempting fix is a bare `#issuecomment-<id>` fragment, and it is wrong:
    Gitea's sanitizer rewrites a relative fragment to
    `#user-content-issuecomment-<id>`, which matches no element on the page, so
    the badge would scroll nowhere at all. The path stays whole.
    """
    provider = _FakeProvider()
    provider.bodies["43"] = "the PR description"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working", refs=(_GITEA_PR_REF,))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.bodies["43"]
    assert "/pulls/43#issuecomment-" in body
    assert "/issues/43#issuecomment-" not in body


def test_an_issue_target_keeps_the_issues_path() -> None:
    provider = _FakeProvider()
    provider.bodies["42"] = "the issue description"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert "/issues/42#issuecomment-" in provider.bodies["42"]


def test_the_status_badge_carries_no_scheme_so_it_inherits_the_readers_own() -> None:
    """A configured `http://` tracker read over `https://` makes an absolute anchor
    cross-origin, and a cross-origin fragment is a full page load rather than a
    scroll — the exact round trip the anchor exists to remove. Protocol-relative
    is same-origin on whichever scheme the reader arrived by.
    """
    provider = _FakeProvider()
    provider.bodies["42"] = "the issue description"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.bodies["42"]
    assert "(//forge.example.com/acme/proj/issues/42#issuecomment-" in body
    assert "https://forge.example.com/acme/proj/issues/42" not in body
    assert "http://forge.example.com/acme/proj/issues/42" not in body


def test_the_footer_is_written_once_and_not_rewritten_when_nothing_moved() -> None:
    """A forge rate-limits, and an edit notifies every watcher of the ticket."""
    provider = _FakeProvider()
    provider.bodies["42"] = "body"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="one")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.body_writes) == 1

    pub.observe(_delta(_row(task="two")))  # the comment changes, the footer does not
    pub.flush_pending(now=T0 + timedelta(seconds=10))
    assert len(provider.body_writes) == 1


def test_the_footer_follows_a_ticket_that_moves_to_another_workspace() -> None:
    """THE handoff case: a ticket outlives the workspace that first held it.

    Nothing hooks the move — the new holder simply publishes, and because both
    badge destinations are derived from whoever holds the ticket at render time,
    the footer rewrites itself. A stored link would have kept pointing at a
    workspace that no longer exists.
    """
    provider = _FakeProvider()
    provider.bodies["42"] = "body"
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="first")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert "/w/ws1" in provider.bodies["42"]

    moved = _row(task="second")
    object.__setattr__(moved.state, "id", "ws2")  # the same ticket, a new workspace
    pub.observe(_delta(moved))
    pub.flush_pending(now=T0 + timedelta(seconds=10))
    assert "/w/ws2" in provider.bodies["42"]
    assert "/w/ws1" not in provider.bodies["42"]  # replaced in place, never duplicated


def test_a_provider_that_cannot_edit_bodies_still_publishes_its_comment() -> None:
    """The footer is decoration; the status update is the job."""
    provider = _FakeProvider(body_supported=False)
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert provider.posts, "the comment must still land"
    assert provider.body_writes == []


def test_publishing_assigns_the_bot_through_the_injected_assigner() -> None:
    """Assignment rides the same edge as the comment — the moment work is known."""
    provider = _FakeProvider()
    seen: list[tuple[str, str, str, str]] = []
    pub = _publisher(provider, window=5.0)
    pub._assigner = lambda root, name, tid, kind: bool(seen.append((root, name, tid, kind))) or True
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert seen == [("/home/u/proj", "gitea", "42", "issue")]


def test_a_failing_assigner_never_breaks_the_publish() -> None:
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)

    def _boom(root: str, name: str, tid: str, kind: str) -> bool:
        raise RuntimeError("forge said no")

    pub._assigner = _boom
    pub.observe(_delta(_row(task="working")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))  # must not raise
    assert provider.posts


def test_the_comment_names_the_branch_the_agent_is_actually_on() -> None:
    """A create-time snapshot rendered as current put a wrong branch on a tracker.

    `WorkspaceState.branch` records what HEAD pointed at when the workspace was
    born and nothing refreshes it, so a workspace whose agent branched afterwards
    published the ORIGINAL branch and its tip commit. ROOT placement makes that
    the normal case, not the exception: Grove creates no branch there, and the
    agent is routinely told to branch off whatever it started on.
    """
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working", branch="feat/live-branch")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = provider.posts[0][1]
    assert "feat/live-branch" in body
    assert "grove/42-fix-auth" not in body


def test_switching_branch_schedules_a_publish() -> None:
    """The stale field never moved, so it silently disabled its own trigger.

    `branch` and the tip commit are two of the fingerprint's members, and the
    commit list is derived from the branch — so while the branch was a frozen
    snapshot, neither could ever change and two of the publish triggers were
    dead. Keying on the live value restores both.
    """
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working", branch="main")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.edits) == 0

    pub.observe(_delta(_row(task="working", branch="feat/switched")))
    pub.flush_pending(now=T0 + timedelta(seconds=10))
    assert provider.edits, "a branch switch must schedule a re-publish"
    assert "feat/switched" in provider.edits[-1][1]


def test_an_absent_live_branch_falls_back_to_the_recorded_one() -> None:
    """Detached HEAD, or a row built before the field existed, must not blank it."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="working")))  # branch="" — nothing derived
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert "grove/42-fix-auth" in provider.posts[0][1]


def test_attaching_a_ticket_publishes_without_waiting_for_the_agent_to_move() -> None:
    """Attach is a render-relevant change, and its absence was a silent forever-bug.

    Every other fingerprint member is a by-product of the agent WORKING — a tool
    call, a reply, a commit, a phase report. Attaching a ticket moves none of
    them while changing what the comment is for: it adds a whole new target
    thread. So a freshly attached ticket got no comment until something
    unrelated happened to move the key, measured at ~16 minutes on a live
    workspace and unbounded in principle — attach to an idle workspace and it
    publishes nothing, forever. It reads as "issue-ops is broken" rather than as
    a delay, which is exactly why it is expensive.
    """
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="steady", refs=(_GITEA_REF,))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1

    # A second ticket is attached. NOTHING else about the workspace changes —
    # same task text, same tool count, same commit, same phase.
    pub.observe(_delta(_row(task="steady", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=10))
    assert [tid for tid, _ in provider.posts] == ["42", "43"]


# ─── the uncapped task text (resolved at dispatch, capped field as fallback) ──


def _activity(body: str) -> str:
    """The activity section's fenced text, or ``""`` when there is no section."""
    section = body.partition("<summary>💬 Latest activity</summary>")[2]
    return section.partition("\n</details>")[0]


def test_the_resolved_task_text_wins_over_the_deltas_capped_field() -> None:
    """The wire field is capped at 500 characters by every adapter because it
    rides the ~1 Hz activity delta for EVERY workspace on the host — correct
    there, a pure loss here, where the comment renders it once per flush behind
    a fold. So the capped field stays capped and this per-request seam answers
    whole: the same split the todo axis already makes (counts on the tick, the
    full list behind a per-request read)."""
    provider = _FakeProvider()
    whole = "the whole story, uncut, as the agent actually wrote it"
    pub = _publisher(provider, window=5.0, task_resolver=lambda root, ws_id: whole)
    pub.observe(_delta(_row(task="the truncat…")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    activity = _activity(provider.posts[0][1])
    assert whole in activity
    assert "the truncat…" not in activity


def test_no_resolver_falls_back_to_the_deltas_own_task_text() -> None:
    """``None`` is a real answer, not a miss — a workspace whose session cannot
    be resolved still has the delta's field, and a comment that renders the
    capped excerpt is strictly better than one that renders nothing."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0, task_resolver=lambda root, ws_id: None)
    pub.observe(_delta(_row(task="the capped excerpt")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert "the capped excerpt" in _activity(provider.posts[0][1])


def test_a_raising_task_resolver_falls_back_and_never_breaks_the_publish() -> None:
    """Best-effort, exactly like the todo and phase reads beside it: this is one
    more dispatch-path read of a workspace that may have been killed under us,
    and a read that costs a nicer excerpt must never cost the update itself —
    let alone re-raise into the activity poll behind it."""

    def boom(root: str, ws_id: str) -> str | None:
        raise RuntimeError("session gone")

    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0, task_resolver=boom)
    pub.observe(_delta(_row(task="the capped excerpt")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))  # must not raise
    assert len(provider.posts) == 1
    assert "the capped excerpt" in _activity(provider.posts[0][1])


# ─── enrichment at dispatch (a bare ref becomes a link and a live status) ─────


class _Clock:
    """A settable clock — the TTL memo is only observable by moving time."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_a_bare_stored_ref_renders_as_a_link_and_a_live_status() -> None:
    """The whole point of resolving refs at dispatch, and the normal path.

    ``attach_ticket`` persists provider + id + kind and nothing else, because
    display enrichment is meant to be an on-demand fetch rather than stale
    stored state — so ``_GITEA_REF`` here carries no url and no status, exactly
    like a real record. Nothing re-resolved them at dispatch, which made the
    render's link branch permanently dead and the status column permanently
    empty: a merged pull request could not read ``merged``, which is the one
    signal a human uses to learn the work landed.
    """
    provider = _FakeProvider(statuses={"43": "merged"})
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="landing", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))

    body = provider.posts[0][1]
    # The enrichment is visible as the TITLE each entry leads with — the render
    # writes the reference bare so the forge links it, so a title is what a
    # resolved ref buys the reader here.
    assert "- ticket 42 — #42" in body
    assert "- ticket 43 — #43" in body
    assert provider.reads == [("issue", "42"), ("pull_request", "43")]


def test_a_pull_request_ref_is_read_from_the_pulls_namespace() -> None:
    """A PR must never go to ``get_ticket``: the issues endpoint reports a
    merged pull request as ``closed`` — true, and useless to a reader deciding
    whether the work landed. Only the pulls namespace carries the merge state,
    so the kind picks the read."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="split", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert provider.reads == [("issue", "42"), ("pull_request", "43")]


def test_enrichment_failure_degrades_to_the_bare_ref_and_still_publishes() -> None:
    """Enrichment is one more best-effort read on the dispatch path, so a forge
    that refuses it costs a link and a status — never the update. Re-raising
    would take the whole sticky comment (and the poll path behind it) down for
    a purely cosmetic read."""
    provider = _FakeProvider(enrich_fails=True)
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="degraded")))
    pub.flush_pending(now=T0 + timedelta(seconds=5))  # must not raise

    assert len(provider.posts) == 1
    body = provider.posts[0][1]
    assert "- #42" in body  # the reference alone, with no title to lead it


def test_dispatch_resolves_the_branch_and_commit_links_from_the_first_target() -> None:
    """Repo-level links come from a provider, so they resolve at dispatch too —
    and a tracker that fronts no repo answers ``None``, which must render as
    plain text rather than a link to nowhere. One body goes to every target, so
    the choice of provider has to be deterministic, not per-target."""
    commit = CommitSummary(sha="abc1234", subject="init parser", committed_at=T0)
    linked = _FakeProvider()
    pub = _publisher(linked, window=5.0)
    pub.observe(_delta(_row(task="linked", commit=commit)))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = linked.posts[0][1]
    assert f"[`grove/42-fix-auth`]({linked.base_url}/src/branch/grove/42-fix-auth)" in body
    assert f"[`abc1234`]({linked.base_url}/commit/abc1234)" in body

    tracker = _FakeProvider(links=False)
    pub = _publisher(tracker, window=5.0)
    pub.observe(_delta(_row(task="unlinked", commit=commit)))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    body = tracker.posts[0][1]
    # No provider scope to name, so the repository falls back to the repo
    # directory's own name rather than the row naming nothing.
    assert "| 🌿 **Branch** | `grove/42-fix-auth` (proj) |" in body
    assert "| 📌 **Commit** | `abc1234` — init parser |" in body


def test_enrichment_is_memoized_so_a_flush_storm_is_one_forge_read() -> None:
    """A working agent flushes about once per window, and enrichment is one GET
    per ref per flush — unmemoized that is thousands of API calls an hour per
    workspace against budgets counted in thousands, buying nothing: a ticket's
    state changes on a human timescale. Memoized at the reader, expiring so the
    comment still learns that a PR merged."""
    clock = _Clock()
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0, clock=clock)

    pub.observe(_delta(_row(task="one")))
    clock.now = T0 + timedelta(seconds=5)
    pub.flush_pending(now=clock.now)
    assert provider.reads == [("issue", "42")]

    pub.observe(_delta(_row(task="two", tool_calls=1)))
    clock.now = T0 + timedelta(seconds=30)
    pub.flush_pending(now=clock.now)
    assert len(provider.edits) == 1  # the comment did update…
    assert provider.reads == [("issue", "42")]  # …off the memo, not a second GET

    pub.observe(_delta(_row(task="three", tool_calls=2)))
    clock.now = T0 + timedelta(seconds=90)  # past the TTL
    pub.flush_pending(now=clock.now)
    assert provider.reads == [("issue", "42"), ("issue", "42")]


def test_render_terminal_summary_is_a_final_outcome_not_a_live_checklist() -> None:
    todo = TodoList(items=(TodoItem(content="still-open", status="pending"),))
    commit = CommitSummary(sha="def5678", subject="final commit", committed_at=T0)
    body = TicketStatusPublisher.render(_snapshot(terminal=True, todo=todo, latest_commit=commit))
    assert SIGNATURE_MARKER in body
    assert "grove/42-fix-auth" in body
    assert "def5678" in body
    # The workspace link is GONE from the finale: the record is deleted with the
    # workspace, so `/w/<id>` resolves to nothing — and this is the render a
    # reader meets months later, which is exactly when a dead link costs most.
    assert "https://grove.example.com/w/ws1" not in body
    # A terminal comment is a final summary — it must not re-render the live
    # checklist a still-open item would show.
    assert "- [ ] still-open" not in body


# ─── the finale outliving its workspace ──────────────────────────────────────


def _links(*specs: tuple[str, str, str | None, bool]) -> tuple[SessionLink, ...]:
    return tuple(
        SessionLink(session_id=sid, kind=kind, url=url, primary=primary)
        for sid, kind, url, primary in specs
    )


def test_the_finale_replaces_the_dead_workspace_link_with_the_transcript() -> None:
    """The whole point: `/w/<id>` dies with the record, and the finale is the
    render a reader meets months later. The transcript is the destination that
    outlives the workspace, so the row becomes that — never both, because the
    workspace link is the one that is guaranteed broken by then."""
    sessions = _links(
        ("abcd1234-5678", "claude_code", "https://grove.example.com/sessions/x", True)
    )
    body = TicketStatusPublisher.render(_snapshot(terminal=True, sessions=sessions))
    assert "| 🧾 **Transcript** | [`abcd1234`](https://grove.example.com/sessions/x) |" in body
    assert "**Workspace**" not in body

    live = TicketStatusPublisher.render(_snapshot(sessions=sessions))
    assert "| 🖥️ **Workspace** | [Open in Grove](https://grove.example.com/w/ws1) |" in live
    assert "**Transcript**" not in live


def test_an_unreachable_transcript_says_so_instead_of_linking() -> None:
    """A link that 404s spends the reader's click, which is worse than the plain
    text it replaced — the `_link` rule, applied to a destination that has to
    survive its workspace. The ordinary case is a CONTAINER workspace: its
    transcript really does survive on the host, under a pinned config dir the
    host-wide session scan never walks, so the bytes exist and the page would
    still 404. Saying that is a fact a reader can act on; a bare id is not."""
    sessions = _links(("abcd1234-5678", "claude_code", None, True))
    body = TicketStatusPublisher.render(_snapshot(terminal=True, sessions=sessions))
    assert "| 🧾 **Transcript** | `abcd1234` — not reachable from here |" in body
    assert "https://grove.example.com/sessions" not in body


def test_the_finale_says_whether_anyone_is_still_working_the_ticket() -> None:
    """ "Session ended" is a fact about a WORKSPACE; a reader on a ticket is
    asking something else, and the two answers come apart exactly when it
    matters. Unanswerable renders NOTHING — the phase rule again: a publisher
    that cannot see the rest of the fleet must not claim the ticket was
    dropped."""
    dropped = TicketStatusPublisher.render(_snapshot(terminal=True, ticket_held=False))
    assert "_No Grove workspace is working this ticket._" in dropped

    picked_up = TicketStatusPublisher.render(_snapshot(terminal=True, ticket_held=True))
    assert "_Another Grove workspace is still working this ticket._" in picked_up

    unknown = TicketStatusPublisher.render(_snapshot(terminal=True))
    assert "working this ticket" not in unknown
    # And never on the live body, where the heading already says who is here.
    assert "working this ticket" not in TicketStatusPublisher.render(_snapshot(ticket_held=False))


def test_sibling_sessions_get_their_own_collapsed_section() -> None:
    """A workspace that ran more than one session shows them all — the primary
    keeps its table row, the rest collapse behind a fixed title. Absent for the
    ordinary one-session workspace rather than rendering an empty section."""
    sessions = _links(
        ("aaaaaaaa-1", "claude_code", "https://g/s/a", True),
        ("bbbbbbbb-2", "codex", "https://g/s/b", False),
        ("cccccccc-3", "claude_code", None, False),
    )
    body = TicketStatusPublisher.render(_snapshot(terminal=True, sessions=sessions))
    assert "<details>\n<summary>🧾 Other sessions</summary>" in body
    assert "- Codex [`bbbbbbbb`](https://g/s/b)" in body
    assert "- Claude Code `cccccccc` — not reachable from here" in body
    # The primary is the table's Transcript row, never repeated in the list.
    assert body.count("aaaaaaaa") == 1

    alone = TicketStatusPublisher.render(_snapshot(terminal=True, sessions=sessions[:1]))
    assert "Other sessions" not in alone


def test_the_finales_facts_are_captured_before_teardown_not_looked_up_after() -> None:
    """The sharp edge of the whole change. By the time a workspace is killed its
    store record is already deleted, so anything the finale needs must have been
    captured while it was alive — which the coalescer's cached row already does.

    Driven the way production does it: one activity delta, then a `killed`
    lifecycle delta, with NO workspace anywhere for a lookup to find."""
    provider = _FakeProvider()
    probed: list[tuple[str, str, str]] = []

    def probe(kind: str, cwd: str, session_id: str) -> bool:
        probed.append((kind, cwd, session_id))
        return True

    pub = _publisher(provider, window=5.0, transcript_probe=probe, ticket_held=False)
    pub.observe(_delta(_row(task="working")))
    pub.observe(_kill_delta())

    body = provider.posts[-1][1]
    assert body.startswith("## ✅ Grove — Session ended")
    assert "_No Grove workspace is working this ticket._" in body
    # The session coordinates came from the cached row, and they are the ones the
    # session surface resolves by: (kind, cwd, session id).
    assert probed == [("claude_code", "/home/u/proj/.worktrees/fix-auth", "s1")]
    assert "| 🧾 **Transcript** | [`s1`]" in body


def test_a_workspace_killed_before_any_activity_still_publishes_nothing_new() -> None:
    """No cached row means nothing to render FROM, and inventing a finale out of
    an id would be a comment asserting facts nobody observed. The workspace is
    latched done so a late delta cannot reopen it."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_kill_delta())
    assert provider.posts == []


# ─── coalescing (fake clock) ───────────────────────────────────────────────────


def test_coalesces_a_burst_into_one_flush_after_the_window() -> None:
    """Many render-relevant deltas inside one window fold into a single PATCH,
    avoiding a same-comment edit storm; the latest state wins."""
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


def test_a_phase_only_change_schedules_a_patch() -> None:
    """A phase set from OUTSIDE the agent must move the render fingerprint.

    The regression this pins: every other member of that fingerprint is a
    by-product of the agent *working* — a tool call, a reply, a commit. A phase
    set through the CLI, MCP or HTTP moves none of them, so while the comment
    rendered the phase, the publisher never scheduled the PATCH that would show
    it. Masked whenever an agent was mid-run, which is exactly why it survived:
    the failure only shows in the two quiet cases the axis exists for —
    "reported, then stopped", and "a human corrected it from outside".
    """
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    row = _row(task="one", tool_calls=1)
    pub.observe(_delta(row))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1

    # Identical agent activity; only the phase moved.
    moved = _row(
        task="one",
        tool_calls=1,
        phase=PhaseReport(phase="verifying", note="running make lint", updated_at=T0),
    )
    pub.observe(_delta(moved))
    pub.flush_pending(now=T0 + timedelta(seconds=20))
    assert provider.edits, "a phase-only change must schedule a comment update"


def test_rewriting_an_identical_phase_does_not_patch() -> None:
    """The counterpart: only ``(phase, note)`` is folded in, never the whole
    report. ``updated_at`` is the phase file's mtime, so an agent rewriting the
    SAME phase would otherwise move the key and buy a redundant PATCH against a
    forge that rate-limits same-comment edits."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    first = _row(
        task="one",
        tool_calls=1,
        phase=PhaseReport(phase="verifying", note="running make lint", updated_at=T0),
    )
    pub.observe(_delta(first))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1

    again = _row(
        task="one",
        tool_calls=1,
        phase=PhaseReport(
            phase="verifying",
            note="running make lint",
            updated_at=T0 + timedelta(minutes=9),
        ),
    )
    pub.observe(_delta(again))
    pub.flush_pending(now=T0 + timedelta(seconds=20))
    assert provider.edits == []


def test_a_per_ticket_claim_change_alone_schedules_a_patch() -> None:
    """The SAME bug class as the phase and ``ticket_refs`` omissions, one level
    down — and this is its third audit, which is the reason it is pinned here
    rather than trusted.

    Every by-product member of the fingerprint answers "is the agent working":
    a tool call, a reply, a commit. An agent that edits ONE entry in its phase
    file moves none of them — not even the workspace's own claim — so leaving
    the per-ticket claims out of the key means a ticket reported as blocked is
    invisible on its own thread until something unrelated happens to move it.
    Unbounded in principle: report a phase, then go quiet, and the comment never
    catches up.

    Everything else here is held byte-identical on purpose, so the ONLY thing
    that can schedule the edit is the claim."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    refs = (_GITEA_REF, _GITEA_PR_REF)
    pub.observe(
        _delta(
            _row(
                task="steady",
                tool_calls=1,
                refs=refs,
                phase=_report("implementing", tickets=(_claim("gitea:43", "implementing"),)),
            )
        )
    )
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 2 and provider.edits == []

    pub.observe(
        _delta(
            _row(
                task="steady",
                tool_calls=1,
                refs=refs,
                phase=_report("implementing", tickets=(_claim("gitea:43", "verifying"),)),
            )
        )
    )
    pub.flush_pending(now=T0 + timedelta(seconds=20))
    assert provider.edits, "a per-ticket claim change must schedule a comment update"


def test_flipping_a_tickets_blocked_flag_alone_schedules_a_patch() -> None:
    """``blocked`` is the single edit most needing a human, and it moves nothing
    else on the row — the phase name does not even change. So it is the member
    whose absence from the key would be both the most invisible and the most
    expensive."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    stuck = _claim("gitea:42", "verifying", blocked=True)
    fine = _claim("gitea:42", "verifying")
    pub.observe(_delta(_row(task="steady", tool_calls=1, phase=_report(tickets=(fine,)))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1 and provider.edits == []

    pub.observe(_delta(_row(task="steady", tool_calls=1, phase=_report(tickets=(stuck,)))))
    pub.flush_pending(now=T0 + timedelta(seconds=20))
    assert provider.edits, "a blocked flag must schedule a comment update"


def test_rewriting_identical_per_ticket_claims_does_not_patch() -> None:
    """The counterpart, and what keeps the new members from becoming a PATCH
    storm: only ``(ticket, phase, note, blocked)`` enters the key, and
    ``PhaseReport`` sorts its claims, so an unchanged file re-read on a later
    tick — a new mtime, a different mapping order — is still clean."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    claims = (_claim("gitea:42", "verifying"), _claim("gitea:43", "delivering"))
    pub.observe(_delta(_row(task="steady", tool_calls=1, phase=_report(tickets=claims))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1

    later = PhaseReport(
        phase="implementing",
        updated_at=T0 + timedelta(minutes=9),  # the file was rewritten, identically
        tickets=claims,
    )
    pub.observe(_delta(_row(task="steady", tool_calls=1, phase=later)))
    pub.flush_pending(now=T0 + timedelta(seconds=20))
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
    site — never re-raised into the poll path."""
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


# ─── multi-target routing (the issue AND the PR that resolves it) ─────────────


def test_publishes_to_every_eligible_target_and_varies_only_the_phase() -> None:
    """A workspace naming an issue and its pull request mirrors onto BOTH threads.

    Almost every fact in the body answers for the WORKSPACE — the agent state,
    the branch, the commit, the checklist, the cross-links — so with no
    per-ticket claim reported the two bodies are byte-identical, which is what
    this pins. The phase is the one exception, and it has its own test."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="mirrored", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert [ticket for ticket, _ in provider.posts] == ["42", "43"]
    assert provider.posts[0][1] == provider.posts[1][1]


def test_each_target_keeps_its_own_sticky_comment() -> None:
    """Two targets, two sticky ids: each later flush edits the comment on its OWN
    thread. With one scalar id per workspace they would edit each other's."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="first", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 2  # one per thread
    pub.observe(_delta(_row(task="second", tool_calls=7, refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=30))
    assert len(provider.posts) == 2  # no duplicate: both were edits
    assert [comment_id for comment_id, _ in provider.edits] == ["c1", "c2"]


def test_duplicate_refs_naming_one_thread_publish_once() -> None:
    """An attach on top of the identical branch-parsed ref is one target, not two —
    deduped by (provider, ticket id) so a thread never gets a second comment."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="doubled", refs=(_GITEA_REF, _GITEA_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 1


def test_an_unconfigured_provider_no_longer_shadows_a_later_ref() -> None:
    """The routing bug: resolution only fails for a *disabled* provider, so an
    enabled-but-tokenless one resolved fine, was taken as THE target, and shadowed
    every later ref — the workspace mirrored onto nothing. Gate on capability."""
    tokenless = _FakeProvider(name="github", configured=False)
    gitea = _FakeProvider(name="gitea")
    pub = _publisher(None, providers=(tokenless, gitea), window=5.0)
    refs = (TicketRef(provider="github", id="9"), _GITEA_REF)
    pub.observe(_delta(_row(task="shadowed", refs=refs)))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert tokenless.posts == []  # no credential → never attempted
    assert [ticket for ticket, _ in gitea.posts] == ["42"]  # the later ref still publishes


def test_a_provider_that_cannot_comment_is_skipped_not_attempted() -> None:
    """Linear backs no comment I/O at all (every write raises
    ``TicketCommentsUnsupported``), so it is not a target — being *enabled* was
    never the same question as being able to carry a comment."""
    linear = _FakeProvider(name="linear", comments_supported=False)
    gitea = _FakeProvider(name="gitea")
    pub = _publisher(None, providers=(linear, gitea), window=5.0)
    refs = (TicketRef(provider="linear", id="ENG-1"), _GITEA_REF)
    pub.observe(_delta(_row(task="keyed", refs=refs)))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert linear.posts == [] and linear.listed == []
    assert len(gitea.posts) == 1


def test_a_failing_target_forgets_only_its_own_comment_id() -> None:
    """One broken thread degrades exactly one comment: the healthy target keeps
    editing its own sticky comment, while the failing one re-scans on the next
    flush (a workspace-wide forget would have made the healthy thread re-scan too,
    and duplicate its comment when the marker read also failed)."""
    provider = _FakeProvider()
    pub = _publisher(provider, window=5.0)
    pub.observe(_delta(_row(task="both-up", refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=5))
    assert len(provider.posts) == 2
    provider._fail_ids = {"43"}  # the PR thread starts failing
    pub.observe(_delta(_row(task="pr-broken", tool_calls=3, refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=30))  # must not raise
    assert [comment_id for comment_id, _ in provider.edits] == ["c1"]  # the issue survived
    # Only the PR's id was forgotten: the issue still edits c1 in place, and the PR
    # re-scans its own thread rather than posting a duplicate onto the issue.
    provider._fail_ids = set()
    pub.observe(_delta(_row(task="pr-back", tool_calls=4, refs=(_GITEA_REF, _GITEA_PR_REF))))
    pub.flush_pending(now=T0 + timedelta(seconds=60))
    assert [comment_id for comment_id, _ in provider.edits] == ["c1", "c1", "c2"]
    assert len(provider.posts) == 2  # recovered by marker scan, never duplicated


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
