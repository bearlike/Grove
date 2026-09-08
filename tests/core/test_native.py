"""Native steering delivery: the channel client and the daemon-backed client.

Two ``NativeSteerClient`` impls, opposite process shapes: ``ChannelSteerClient``
is the PANELESS backend's (messages only, best-effort), ``DaemonSteerClient``
is the out-of-process road a CLI/TUI manager takes to a native workspace's
owner (every op POSTs a daemon steer route). A refusal on ``interrupt`` /
``set_model`` RAISES — a silent no-op there is a control that looks wired and
is not, which is the bug this seam replaced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from grove.core import channel, native
from grove.core.errors import AgentSessionNotFound, SteeringUnsupported
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(session_id: str | None = "sess-1") -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id="a" * 32,
        title="t",
        repo_root="/repo",
        branch="b",
        base_branch="main",
        worktree_path="/repo/.worktrees/t",
        tmux_session="grove-t",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        agent_session_id=session_id,
        native=True,
    )


# ─── ChannelSteerClient: best-effort delivery over the channel receiver ──────


def test_send_message_no_endpoint_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No published endpoint file (channels off / server down) → a logged no-op,
    never a raise, and no HTTP attempt."""
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: tmp_path / "absent.json")

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("no POST should be attempted without an endpoint")

    monkeypatch.setattr(httpx, "post", _boom)
    native.ChannelSteerClient().send_message(_state(), "hi")  # must not raise


def test_send_message_posts_to_receiver_with_bearer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    endpoint = tmp_path / "channel-endpoint.json"
    endpoint.write_text(json.dumps({"port": 54321, "token": "tok"}), encoding="utf-8")
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: endpoint)
    captured: dict[str, object] = {}

    def _fake_post(url: str, **kwargs: object) -> object:
        captured.update({"url": url, **kwargs})

        class _Resp:
            status_code = 202

        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    native.ChannelSteerClient().send_message(_state(), "hello")

    assert captured["url"] == f"http://127.0.0.1:54321{channel.RECEIVE_ROUTE}"
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["content"] == "hello"
    assert body["meta"] == {"session_id": "sess-1"}


def test_send_message_swallows_http_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A transport failure is best-effort: logged and swallowed, never raised into
    the steering caller's path."""
    endpoint = tmp_path / "channel-endpoint.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}), encoding="utf-8")
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: endpoint)

    def _raise(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    native.ChannelSteerClient().send_message(_state(), "hello")  # must not raise


def test_channel_client_needs_a_recorded_session() -> None:
    with pytest.raises(AgentSessionNotFound):
        native.ChannelSteerClient().send_message(_state(None), "hi")


@pytest.mark.parametrize("op", ["interrupt", "set_model"])
def test_channel_client_refuses_controls_loudly(op: str) -> None:
    """A channel carries text only; a control that silently did nothing would read
    as a working control, so the refusal is the typed 501 the daemon maps."""
    client = native.ChannelSteerClient()
    with pytest.raises(SteeringUnsupported):
        if op == "interrupt":
            client.interrupt(_state())
        else:
            client.set_model(_state(), "opus")


# ─── DaemonSteerClient: every op is a daemon steer route ────────────────────


class _Resp:
    def __init__(self, status_code: int, body: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self.content = b"x" if body is not None else b""
        self.text = json.dumps(body or {})
        self._body = body or {}

    def json(self) -> dict[str, object]:
        return self._body


@pytest.fixture
def local_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the same-host pairing mint; the bearer's shape is not under test."""
    monkeypatch.setattr(native.DaemonSteerClient, "_bearer", lambda _self: "local-tok")


@pytest.mark.usefixtures("local_bearer")
@pytest.mark.parametrize(
    ("op", "verb", "body"),
    [
        ("send_message", "message", {"text": "hi"}),
        ("interrupt", "interrupt", None),
        ("set_model", "controls/model", {"model": "opus"}),
    ],
)
def test_daemon_client_posts_the_matching_steer_route(
    monkeypatch: pytest.MonkeyPatch, op: str, verb: str, body: dict[str, str] | None
) -> None:
    captured: dict[str, object] = {}

    def _fake_post(url: str, **kwargs: object) -> object:
        captured.update({"url": url, **kwargs})
        return _Resp(204)

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = native.DaemonSteerClient("http://127.0.0.1:7421/")
    state = _state()
    if op == "send_message":
        client.send_message(state, "hi")
    elif op == "interrupt":
        client.interrupt(state)
    else:
        client.set_model(state, "opus")
    assert captured["url"] == f"http://127.0.0.1:7421/workspaces/{state.id}/{verb}"
    assert captured["json"] == body
    assert captured["headers"] == {"Authorization": "Bearer local-tok"}


@pytest.mark.usefixtures("local_bearer")
def test_daemon_client_surfaces_a_refusal_as_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon's refusal (no owner connected → 409) reaches the caller with the
    daemon's own message, never as a swallowed 'delivered'."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_k: _Resp(
            409, {"detail": {"error": "pane_not_found", "message": "no connected native owner"}}
        ),
    )
    with pytest.raises(SteeringUnsupported, match="no connected native owner"):
        native.DaemonSteerClient("http://127.0.0.1:7421").interrupt(_state())


@pytest.mark.usefixtures("local_bearer")
def test_daemon_client_names_an_unreachable_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    with pytest.raises(SteeringUnsupported, match="did not answer"):
        native.DaemonSteerClient("http://127.0.0.1:1").set_model(_state(), "opus")
