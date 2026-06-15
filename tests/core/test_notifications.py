"""NotificationBroker edge/debounce policy + the Gotify / webhook channels.

The broker's ``evaluate`` is the single pure decision site, so it is tested
directly against a fake clock with hand-built deltas (no threads, no I/O). The
channels are tested against ``httpx.MockTransport`` — the same "stub only the I/O
boundary" seam ``MewboClient`` uses — asserting the exact wire shape and that a
failure narrows to the typed error the broker's guard expects.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.config import GotifyChannelConfig, GroveConfig, WebhookChannelConfig
from grove.core.errors import GotifyError, WebhookError
from grove.core.notifications import (
    GotifyNotificationChannel,
    Notification,
    NotificationBroker,
    NotificationChannel,
    WebhookNotificationChannel,
)
from grove.core.workspace import WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)


# ─── builders ────────────────────────────────────────────────────────────────


def _ws_state() -> WorkspaceState:
    return WorkspaceState(
        id="ws1",
        title="fix-auth",
        repo_root="/home/u/proj",
        branch="grove/fix-auth",
        base_branch="main",
        worktree_path="/home/u/proj/.worktrees/fix-auth",
        tmux_session="grove-fix-auth",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=T0,
        updated_at=T0,
    )


def _session(
    state: AgentActivityState, *, sid: str = "s1", task: str | None = None
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id=sid,
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(state=state, current_task=task),
    )


def _delta(*sessions: SessionActivity, kind: str = "session_activity") -> DashboardDelta:
    row = WorkspaceActivity(
        state=_ws_state(),
        sessions=tuple(sessions),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=T0,
    )
    return DashboardDelta(
        kind=kind,  # type: ignore[arg-type]
        seq=1,
        workspace_id="ws1",
        repo_root="/home/u/proj",
        workspace=row if kind == "session_activity" else None,
    )


def _broker(**kw: object) -> NotificationBroker:
    clock = kw.pop("clock", lambda: T0)
    return NotificationBroker(
        channels=[], deep_link_base_url="https://grove.example.com", clock=clock, **kw
    )  # type: ignore[arg-type]


# ─── edge-trigger policy ───────────────────────────────────────────────────────


def test_first_observation_seeds_without_firing() -> None:
    """A session first seen already WAITING (daemon restart mid-turn) seeds the
    edge memory but must not buzz — otherwise every finished workspace fires on boot."""
    broker = _broker()
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []


def test_rising_edge_into_waiting_fires() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed WORKING
    out = broker.evaluate(_delta(_session(AgentActivityState.WAITING, task="ran the suite")))
    assert len(out) == 1
    n = out[0]
    assert n.state is AgentActivityState.WAITING
    assert n.workspace_id == "ws1"
    assert n.reason == "finished its turn"
    assert n.summary == "ran the suite"
    assert n.deep_link == "https://grove.example.com/w/ws1"
    assert n.title() == "proj · fix-auth"
    assert "claude finished its turn" in n.body()


def test_staying_in_attention_state_does_not_refire() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1
    # Still WAITING next tick → no new edge.
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []


def test_working_state_never_fires() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))  # seed (no fire)
    assert broker.evaluate(_delta(_session(AgentActivityState.WORKING))) == []


def test_workspace_changed_delta_is_ignored() -> None:
    """Lifecycle deltas carry no session payload — nothing to edge-detect."""
    broker = _broker()
    assert broker.evaluate(_delta(kind="workspace_changed")) == []


def test_blocked_and_error_are_default_targets() -> None:
    # debounce=0 so the two distinct edges in one test instant aren't suppressed.
    broker = _broker(debounce=timedelta(0))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert (
        broker.evaluate(_delta(_session(AgentActivityState.BLOCKED)))[0].reason
        == "needs your input"
    )
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.ERROR)))[0].reason == "hit an error"


def test_debounce_suppresses_second_fire_within_window() -> None:
    """After a fire the workspace is quiet for the debounce window, even across a
    WAITING→WORKING→WAITING flap."""
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], debounce=timedelta(seconds=30))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1
    # 10s later: flap back to WORKING then WAITING — still inside the window.
    clock["t"] = T0 + timedelta(seconds=10)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []
    # Past the window: a fresh edge fires again.
    clock["t"] = T0 + timedelta(seconds=40)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1


def test_custom_notify_states_can_include_idle_and_exclude_error() -> None:
    broker = _broker(notify_states=frozenset({AgentActivityState.IDLE}))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.ERROR))) == []  # not a target
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.IDLE)))) == 1


def test_non_attention_notify_states_are_filtered_out() -> None:
    """WORKING/STARTING can never be notify targets even if misconfigured."""
    broker = _broker(notify_states=frozenset({AgentActivityState.WORKING}))
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WORKING))) == []


def test_no_deep_link_base_yields_none() -> None:
    broker = NotificationBroker(channels=[], clock=lambda: T0)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING)))[0].deep_link is None


# ─── deliver fan-out (best-effort isolation) ──────────────────────────────────


class _FlakyChannel(NotificationChannel):
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, notification: Notification) -> None:
        self.calls += 1
        raise GotifyError("boom")


class _CapturingChannel(NotificationChannel):
    name = "capture"

    def __init__(self) -> None:
        self.received: list[Notification] = []

    def deliver(self, notification: Notification) -> None:
        self.received.append(notification)


def test_dispatch_isolates_a_failing_channel() -> None:
    """A channel that raises never blocks the others (the activity-path guard)."""
    flaky, good = _FlakyChannel(), _CapturingChannel()
    broker = NotificationBroker(channels=[flaky, good], clock=lambda: T0)
    n = Notification(
        workspace_id="ws1",
        workspace_title="t",
        repo_name="proj",
        branch="b",
        agent_name="claude",
        state=AgentActivityState.WAITING,
        reason="finished its turn",
        summary=None,
        deep_link=None,
        occurred_at=T0,
    )
    broker.dispatch(n)  # must not raise
    assert flaky.calls == 1
    assert good.received == [n]


class _FakeBus:
    """A minimal stand-in for ``ActivityService.subscribe`` — one subscriber."""

    def __init__(self) -> None:
        self.callback: object = None

    def subscribe(self, callback: object) -> object:
        self.callback = callback
        return lambda: setattr(self, "callback", None)


def test_bind_delivers_edge_through_the_dispatch_pool() -> None:
    """End-to-end of the async path: a bus delta on the rising edge reaches the
    channel via the dispatch worker, and ``close`` unsubscribes + stops it."""
    bus = _FakeBus()
    channel = _CapturingChannel()
    broker = NotificationBroker(channels=[channel], clock=lambda: T0)
    broker.bind(bus.subscribe)  # type: ignore[arg-type]
    try:
        assert callable(bus.callback)
        bus.callback(_delta(_session(AgentActivityState.WORKING)))  # type: ignore[operator]  # seed
        bus.callback(_delta(_session(AgentActivityState.WAITING)))  # type: ignore[operator]  # fire
        deadline = time.monotonic() + 2.0
        while not channel.received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(channel.received) == 1
        assert channel.received[0].state is AgentActivityState.WAITING
    finally:
        broker.close()
    assert bus.callback is None  # unsubscribed on close


# ─── Gotify channel ───────────────────────────────────────────────────────────


def _notification(deep_link: str | None = "https://grove.example.com/w/ws1") -> Notification:
    return Notification(
        workspace_id="ws1",
        workspace_title="fix-auth",
        repo_name="proj",
        branch="grove/fix-auth",
        agent_name="claude",
        state=AgentActivityState.WAITING,
        reason="finished its turn",
        summary="ran the suite",
        deep_link=deep_link,
        occurred_at=T0,
    )


def test_gotify_posts_message_with_token_and_click(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "secret-app-token")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("X-Gotify-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 1})

    cfg = GotifyChannelConfig(enabled=True, server_url="https://gotify.example.com/", priority=7)
    channel = GotifyNotificationChannel(cfg, transport=httpx.MockTransport(handler))
    channel.deliver(_notification())

    assert captured["url"] == "https://gotify.example.com/message"
    assert captured["key"] == "secret-app-token"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["title"] == "proj · fix-auth"
    assert body["priority"] == 7
    assert "claude finished its turn" in body["message"]
    assert (
        body["extras"]["client::notification"]["click"]["url"] == "https://grove.example.com/w/ws1"
    )


def test_gotify_omits_click_when_no_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com")
    channel = GotifyNotificationChannel(cfg, transport=httpx.MockTransport(handler))
    channel.deliver(_notification(None))
    assert "extras" not in captured["body"]  # type: ignore[operator]


def test_gotify_raises_typed_error_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com")
    channel = GotifyNotificationChannel(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized"))
    )
    with pytest.raises(GotifyError):
        channel.deliver(_notification())


# ─── webhook channel ──────────────────────────────────────────────────────────


def test_webhook_posts_json_with_topic_and_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_NTFY_TOKEN", "tk_abc")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    cfg = WebhookChannelConfig(
        enabled=True, url="https://ntfy.sh", token_env="GROVE_NTFY_TOKEN", topic="grove-alerts"
    )
    WebhookNotificationChannel(cfg, transport=httpx.MockTransport(handler)).deliver(_notification())

    assert captured["url"] == "https://ntfy.sh"
    assert captured["auth"] == "Bearer tk_abc"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["topic"] == "grove-alerts"
    assert body["click"] == "https://grove.example.com/w/ws1"
    assert body["state"] == "waiting"
    assert body["workspace_id"] == "ws1"


def test_webhook_raises_typed_error_on_5xx() -> None:
    cfg = WebhookChannelConfig(enabled=True, url="https://h.example.com/hook")
    channel = WebhookNotificationChannel(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(WebhookError):
        channel.deliver(_notification())


# ─── from_config factory ────────────────────────────────────────────────────


def test_from_config_disabled_returns_none() -> None:
    assert NotificationBroker.from_config(GroveConfig().notifications) is None


def test_from_config_enabled_with_no_channel_returns_none() -> None:
    cfg = GroveConfig.model_validate({"notifications": {"enabled": True}})
    assert NotificationBroker.from_config(cfg.notifications) is None


def test_from_config_builds_only_enabled_channels_and_resolves_states() -> None:
    cfg = GroveConfig.model_validate(
        {
            "notifications": {
                "enabled": True,
                "on": ["waiting", "error"],
                "gotify": {"enabled": True, "server_url": "https://g.example.com"},
                "webhook": {"enabled": False, "url": "https://h.example.com"},
            }
        }
    )
    broker = NotificationBroker.from_config(cfg.notifications)
    assert broker is not None
    assert [c.name for c in broker.channels] == ["gotify"]
    # The "on" set coerced to the enum and seeded the edge detector.
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED))) == []  # not in "on"
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.ERROR)))) == 1
