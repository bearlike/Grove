"""Grove channel: the pure envelope/policy layer + the launch composition.

The live stdio transport (``ChannelServer.run`` and the SDK wiring) is not unit
tested — a real MCP handshake, same as ``GroveMcpServer.run``. Everything it
composes (message parsing, the fail-closed allowlist + permission relay, the
loopback receiver's accept/reject decision, the settings declaration) is pure
and pinned here, plus the manager's opt-in ``--channels`` decoration append.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

from grove.core import channel
from grove.core.admission import AdmissionLimits, BoundedInbox
from grove.core.channel import (
    RECEIVE_ROUTE,
    ChannelDeliveryLedger,
    ChannelDeliveryStatus,
    ChannelEndpoint,
    ChannelMessage,
    ChannelPolicy,
    ChannelReceiver,
    ChannelServer,
    PermissionDecision,
    channel_settings,
    ensure_channel_token,
)
from grove.core.config import ChannelsConfig, GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

# ─── config ─────────────────────────────────────────────────────────────────


def test_channels_config_defaults_off_and_permissive_allowlist() -> None:
    cfg = ChannelsConfig()
    assert cfg.enabled is False
    assert cfg.allowed_senders == []
    # Registered on the top-level config with the same off default.
    assert GroveConfig().channels.enabled is False


# ─── ChannelMessage ─────────────────────────────────────────────────────────


def test_message_from_payload_parses_content_meta_sender() -> None:
    msg = ChannelMessage.from_payload({"content": "hi", "meta": {"k": 1}, "sender": "cli"})
    assert msg is not None
    assert msg.content == "hi"
    assert msg.meta == {"k": 1}
    assert msg.sender == "cli"
    # Emitted params carry only content + meta (sender is Grove-side attribution).
    assert msg.to_notification_params() == {"content": "hi", "meta": {"k": 1}}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not a dict",
        {},  # no content
        {"content": ""},  # empty content
        {"content": 123},  # non-string content
    ],
)
def test_message_from_payload_rejects_malformed(raw: object) -> None:
    assert ChannelMessage.from_payload(raw) is None


def test_message_coerces_bad_meta_and_sender_to_defaults() -> None:
    msg = ChannelMessage.from_payload({"content": "x", "meta": "nope", "sender": ""})
    assert msg is not None
    assert msg.meta == {}  # non-dict meta → empty
    assert msg.sender is None  # empty sender → unattributed


# ─── ChannelPolicy: allowlist (fail-closed) ─────────────────────────────────


def test_empty_allowlist_permits_everyone() -> None:
    policy = ChannelPolicy(())
    assert policy.permits_sender("anyone") is True
    assert policy.permits_sender(None) is True


def test_populated_allowlist_is_strict_membership() -> None:
    policy = ChannelPolicy(("cli", "web"))
    assert policy.permits_sender("cli") is True
    assert policy.permits_sender("web") is True
    assert policy.permits_sender("mcp") is False
    # A named gate cannot be passed anonymously.
    assert policy.permits_sender(None) is False


# ─── ChannelPolicy: permission relay (fail-closed until wired) ──────────────


def test_permission_relay_denies_by_default() -> None:
    decision = ChannelPolicy(()).resolve_permission({"tool_name": "Bash"})
    assert decision.behavior == PermissionDecision.DENY
    result = decision.to_result()
    assert result["behavior"] == "deny"
    assert "Bash" in result["message"]


def test_permission_decision_allow_omits_message() -> None:
    decision = PermissionDecision(behavior=PermissionDecision.ALLOW)
    assert decision.to_result() == {"behavior": "allow"}


# ─── Channel delivery ledger ─────────────────────────────────────────────────


def test_delivery_ledger_records_failure_without_message_contents() -> None:
    ledger = ChannelDeliveryLedger()
    message = ChannelMessage(content="do not log this", meta={"also": "private"})
    ledger.record(message, ChannelDeliveryStatus.FAILED)
    assert ledger.outcomes == ((ChannelDeliveryStatus.FAILED, len(message.content)),)


def test_delivery_ledger_records_shutdown_pending_and_in_flight() -> None:
    ledger = ChannelDeliveryLedger()
    ledger.record(ChannelMessage(content="pending"), ChannelDeliveryStatus.UNDLVRD_PENDING)
    ledger.record(ChannelMessage(content="in flight"), ChannelDeliveryStatus.UNDLVRD_IN_FLIGHT)
    assert ledger.outcomes == (
        (ChannelDeliveryStatus.UNDLVRD_PENDING, len("pending")),
        (ChannelDeliveryStatus.UNDLVRD_IN_FLIGHT, len("in flight")),
    )


# ─── ChannelReceiver.handle_body: pure accept/reject ────────────────────────


@pytest.mark.asyncio
async def test_receiver_rejects_bad_token_before_admission() -> None:
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, AdmissionLimits())
    status, _ = receiver.handle_body(authorization="Bearer wrong", body=b'{"content":"x"}')
    assert status == 401
    assert inbox.stats().items == 0


@pytest.mark.asyncio
async def test_receiver_rejects_invalid_json_and_malformed_before_admission() -> None:
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, AdmissionLimits())
    assert receiver.handle_body(authorization="Bearer secret", body=b"{oops")[0] == 400
    assert receiver.handle_body(authorization="Bearer secret", body=b'{"no":"content"}')[0] == 400
    assert inbox.stats().items == 0


@pytest.mark.asyncio
async def test_a_stalled_body_cannot_wedge_the_single_threaded_receiver() -> None:
    """A half-sent request must cost its own connection, never the channel.

    The receiver is deliberately single-threaded (a channel into a running agent
    must not spawn a thread per connection), so a client that declares an
    in-cap Content-Length and then stops sending blocks `rfile.read` with no
    socket timeout — and every later legitimate delivery queues behind it
    forever. Bounding the read is what keeps "synchronous handling" from
    meaning "any local process can silence the channel by opening a socket".

    Driven over a REAL socket: the defect is in the transport, and a direct
    `handle_body` call never reaches the read that blocks.
    """
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, AdmissionLimits())
    server, port = receiver.serve(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        stalled = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            # Declare a body and never send it.
            stalled.sendall(
                f"POST {RECEIVE_ROUTE} HTTP/1.1\r\nHost: x\r\nContent-Length: 12\r\n\r\n".encode()
            )
            # A well-behaved client must still be served while that one hangs.
            good = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                body = b'{"content":"x"}'
                good.sendall(
                    f"POST {RECEIVE_ROUTE} HTTP/1.1\r\nHost: x\r\n"
                    f"Authorization: Bearer secret\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                good.settimeout(10)
                assert b" 202 " in good.recv(256), "the stalled peer wedged the channel"
            finally:
                good.close()
        finally:
            stalled.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.mark.asyncio
async def test_receiver_rejects_disallowed_sender_before_admission() -> None:
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(("cli",)), "secret", inbox, AdmissionLimits())
    status, _ = receiver.handle_body(
        authorization="Bearer secret", body=b'{"content":"x","sender":"evil"}'
    )
    assert status == 403
    assert inbox.stats().items == 0


@pytest.mark.asyncio
async def test_receiver_accepts_ordered_messages_without_coalescing() -> None:
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(("cli",)), "secret", inbox, AdmissionLimits())
    for content in ("first", "second"):
        status, _ = receiver.handle_body(
            authorization="Bearer secret",
            body=f'{{"content":"{content}","sender":"cli"}}'.encode(),
        )
        assert status == 202
    first = await inbox.take()
    second = await inbox.take()
    assert [first.value.content, second.value.content] == ["first", "second"]
    assert inbox.stats().coalesced == 0
    inbox.complete(first)
    inbox.complete(second)


@pytest.mark.asyncio
async def test_receiver_refuses_overloaded_and_too_large_messages() -> None:
    limits = AdmissionLimits(max_items=1, max_bytes=32)
    inbox = BoundedInbox[ChannelMessage](limits)
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, limits)
    assert receiver.handle_body(authorization="Bearer secret", body=b'{"content":"x"}')[0] == 202
    assert receiver.handle_body(authorization="Bearer secret", body=b'{"content":"y"}')[0] == 503
    assert (
        receiver.handle_body(
            authorization="Bearer secret", body=b'{"content":"too large for channel"}'
        )[0]
        == 413
    )
    pending = inbox.close()
    assert [delivery.value.content for delivery in pending] == ["x"]


@pytest.mark.asyncio
async def test_receiver_refuses_when_inbox_is_not_ready_or_closed() -> None:
    inbox = BoundedInbox[ChannelMessage](AdmissionLimits())
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, AdmissionLimits())
    assert receiver.handle_body(authorization="Bearer secret", body=b'{"content":"x"}')[0] == 503
    inbox.bind()
    inbox.close()
    assert receiver.handle_body(authorization="Bearer secret", body=b'{"content":"x"}')[0] == 503


@pytest.mark.asyncio
async def test_receiver_refuses_content_length_before_reading_body() -> None:
    limits = AdmissionLimits(max_bytes=10)
    inbox = BoundedInbox[ChannelMessage](limits)
    inbox.bind()
    receiver = ChannelReceiver(ChannelPolicy(()), "secret", inbox, limits)
    assert receiver.acquire_request(content_length="11") == (413, "message too large")
    assert receiver.acquire_request(content_length="malformed") == (400, "invalid content length")
    assert receiver.acquire_request(content_length="10") is None
    receiver.release_request()


# ─── settings declaration + endpoint record + token ─────────────────────────


def test_channel_settings_declares_experimental_capability() -> None:
    grove = channel_settings()["channels"]["grove"]
    assert grove["args"] == ["-m", "grove.core.channel"]
    assert grove["capabilities"]["experimental"] == {channel.CAPABILITY_KEY: {}}


def test_server_capabilities_advertise_channel_key() -> None:
    server = ChannelServer(ChannelsConfig(allowed_senders=["cli"]))
    assert server.capabilities() == {channel.CAPABILITY_KEY: {}}
    assert server.policy.permits_sender("cli") is True
    assert server.policy.permits_sender("other") is False


def test_channel_endpoint_round_trips() -> None:
    ep = ChannelEndpoint(port=54321, token="tok")
    assert ChannelEndpoint.from_json(ep.to_json()) == ep
    assert ChannelEndpoint.from_json({"port": "bad", "token": "t"}) is None
    assert ChannelEndpoint.from_json("nope") is None


def test_ensure_channel_token_persists_and_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "channel_settings_path", lambda: tmp_path / "s.json")
    first = ensure_channel_token()
    assert first
    assert ensure_channel_token() == first  # get-or-create, stable across calls


# ─── manager launch composition: opt-in --channels append ───────────────────


def _last_decoration(fake: FakeTmux, session: str) -> list[str]:
    for name, decoration in reversed(fake.launch_decorations):
        if name == session:
            return decoration
    raise AssertionError(f"no launch decoration recorded for {session}")


def _manager(tmp_repo: Path, tmp_path: Path, *, channels: bool) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            # Same reason, one feature over: with no hook to inject it, the
            # first-turn brief rides the initial prompt and would prefix the
            # exact positionals asserted here (see `test_agent_brief.py`).
            "brief": {"enabled": False},
            "channels": {"enabled": channels},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_channels_disabled_appends_no_flag(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, channels=False)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="off"))
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
    ]


def test_channels_enabled_appends_channels_flag_before_prompt(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``--channels`` flag rides a claude_code launch when enabled, and stays
    BEFORE the trailing initial-prompt positional (mirrors the hook --settings)."""
    settings = tmp_path / "channel-settings.json"
    monkeypatch.setattr(channel, "channel_settings_path", lambda: settings)
    mgr = _manager(tmp_repo, tmp_path, channels=True)
    state = mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title="on", initial_prompt="do it")
    )
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--channels",
        str(settings),
        "do it",
    ]
    assert settings.exists()  # settings file written at launch


def test_channels_flag_not_appended_for_shell_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Non-claude_code kinds never get the flag (no channel concept)."""
    mgr = _manager(tmp_repo, tmp_path, channels=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="shell", title="sh"))
    assert _last_decoration(fake_tmux, state.tmux_session) == []
