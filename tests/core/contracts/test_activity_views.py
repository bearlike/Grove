"""The activity wire shapes, and the one relation clients gate their polling on.

``WorkspaceActivityView`` (the payload of a ``session_activity`` SSE frame) is a
strict superset of ``WorkspacePeekView`` minus the pane capture. That is what
lets a client stop polling ``GET /workspaces/{id}/peek`` while a stream is
healthy — so it is a contract, not a coincidence, and nothing in the type
system says so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.activity import FleetSummary, SessionActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.agents.hook import SubagentHookRecord
from grove.core.contracts.activity import (
    FleetProgressView,
    SessionActivityView,
    SubagentActivityView,
    WorkspaceActivityView,
)
from grove.core.contracts.usage import DurationView, TokenClassesView
from grove.core.contracts.views import WorkspacePeekView

# The pane capture deliberately does NOT ride the cross-project activity fan-out
# (it is per-workspace, per-focus and ~1 Hz); its push channel is the dedicated
# `GET /workspaces/{id}/pane/stream`. See daemon/CLAUDE.md.
_PANE_ONLY = {"agent_snapshot", "snapshot_taken_at"}


def test_session_activity_frame_covers_every_peek_field_but_the_pane() -> None:
    peek = set(WorkspacePeekView.model_fields)
    streamed = set(WorkspaceActivityView.model_fields)
    assert peek - streamed == _PANE_ONLY


def test_the_shared_peek_fields_are_the_SAME_type_on_both_views() -> None:
    """A same-named field of a different type would be a superset in name only.

    ``state`` is the one that matters: both sides must hand a client the same
    ``WorkspaceStateView``, or "read it off the stream instead" silently drops
    fields the polled shape carried.
    """
    for name in set(WorkspacePeekView.model_fields) - _PANE_ONLY:
        polled = WorkspacePeekView.model_fields[name].annotation
        streamed = WorkspaceActivityView.model_fields[name].annotation
        assert polled == streamed, name


# ─── sub-agent fleet wire mirrors ────────────────────────────────────────────


def test_subagent_activity_view_from_record() -> None:
    now = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)
    record = SubagentHookRecord(
        session_id="s",
        agent_id="a-1",
        agent_type="general-purpose",
        state=AgentActivityState.WORKING,
        event="PreToolUse",
        started_at=now,
        last_event_at=now,
        current_tool="Bash",
        last_message=None,
    )

    view = SubagentActivityView.from_record(record)

    assert view.agent_id == "a-1"
    assert view.agent_type == "general-purpose"
    assert view.state is AgentActivityState.WORKING
    assert view.current_tool == "Bash"
    assert view.last_message is None


def test_fleet_progress_view_from_summary() -> None:
    view = FleetProgressView.from_summary(FleetSummary(active=1, total=3))

    assert view.active == 1
    assert view.total == 3


# ─── session duration on the wire ────────────────────────────────────────────


def _session_activity(
    duration: DurationView | None, tokens: TokenClassesView | None = None
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id="s1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(state=AgentActivityState.WAITING),
        duration=duration,
        tokens=tokens,
    )


def test_session_activity_view_carries_duration_through() -> None:
    """`SessionActivityView.duration` mirrors `SessionActivity.duration`
    verbatim — the same `DurationView` the session catalog and the
    project-scoped listing already publish, so a workspace card and a
    session-browse row can never disagree about one session's clocks."""
    duration = DurationView(
        active_ms=1000, execution_ms=2500, elapsed_span_ms=1200, confidence="derived"
    )

    view = SessionActivityView.from_session_activity(_session_activity(duration))

    assert view.duration == duration


def test_session_activity_view_duration_defaults_to_none() -> None:
    """`None` means "not measured yet", never a fabricated zero — and it is
    also the additive-wire-evolution default, so a pre-existing client
    decodes a payload with no `duration` key at all unchanged."""
    view = SessionActivityView.from_session_activity(_session_activity(None))

    assert view.duration is None
    assert SessionActivityView.model_fields["duration"].default is None


# ─── session token classes on the wire ───────────────────────────────────────


def test_session_activity_view_carries_token_classes_through() -> None:
    """`SessionActivityView.tokens` mirrors `SessionActivity.tokens`
    verbatim — the classes that sum to `activity.tokens_in`, kept apart rather
    than folded, so a workspace card can explain a large "tokens in" figure
    instead of only reporting it."""
    tokens = TokenClassesView(fresh_input=100, cache_read=250_000_000, cache_creation=5_000)

    view = SessionActivityView.from_session_activity(_session_activity(None, tokens))

    assert view.tokens == tokens


def test_session_activity_view_tokens_defaults_to_none() -> None:
    """`None` means "not measured yet", never a fabricated zero — and it is
    also the additive-wire-evolution default, so a pre-existing client
    decodes a payload with no `tokens` key at all unchanged."""
    view = SessionActivityView.from_session_activity(_session_activity(None))

    assert view.tokens is None
    assert SessionActivityView.model_fields["tokens"].default is None
