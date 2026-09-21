"""Native steering — deliver a message, interrupt or model switch to an agent with no pane.

The tmux backend types steering into a live pane (``tmux.send_text`` /
``send_keys``). Two runtimes have no pane to type into and take this seam
instead: a **native** workspace (``WorkspaceState.native`` — Grove launched the
agent on its own protocol and a worker process holds the control channel), and
a **paneless** runtime (``provides_pane = False``, a headless detached process
reachable only over the Grove channel receiver). The manager's ``_steer_native``
is the ``_steer_remote`` mirror, injected the same way (``mewbo_client`` ↔
``native_steer``) so a test drives it with a fake and no socket.

Two clients, one Protocol, and which one a process holds is what keeps the
daemon from calling itself: the daemon injects its coordinator-backed client
(``grove.daemon.mailboxes.OwnerSteerClient``) into every manager it
mints, so a route reaches the owner worker in-process; the CLI and TUI build
managers with no injection and fall through to :class:`DaemonSteerClient`,
which POSTs the same verbs to the daemon's ordinary workspace routes. The
channel receiver keeps :class:`ChannelSteerClient` for the paneless backend
alone. Dependencies flow inward — this imports ``channel`` and ``auth``, never
the manager.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from grove.core import channel
from grove.core.errors import AgentSessionNotFound, SteeringUnsupported

if TYPE_CHECKING:
    from grove.core.workspace import WorkspaceState

# The loopback POSTs are same-host and near-instant; a tight timeout keeps a
# stalled receiver or daemon from ever blocking a steering call.
_DELIVER_TIMEOUT_SECONDS = 5.0

# Grove's declared sender identity on a channel delivery (matches the channel
# allowlist's expectations; empty allowlist accepts it regardless).
_SENDER = "grove"


class NativeSteerClient(Protocol):
    """Deliver steering to an agent that has no pane, keyed by its workspace.

    Three ops. ``send_message`` is best-effort like the mewbo remote surface:
    it logs and returns on a transport failure. ``interrupt`` and ``set_model``
    RAISE a typed ``GroveError`` when the transport has no such primitive or no
    owner is connected — a silent no-op there reads as a working control, which
    is the failure this seam replaced.
    """

    def send_message(self, state: WorkspaceState, text: str) -> None: ...

    def interrupt(self, state: WorkspaceState) -> None: ...

    def set_model(self, state: WorkspaceState, model: str) -> None: ...

    def compact(self, state: WorkspaceState) -> None: ...

    def invoke_control(self, state: WorkspaceState, name: str) -> None: ...

    def answer(self, state: WorkspaceState, plan: str) -> None:
        """Resolve the standing ask; ``plan`` is the manager's rendered JSON."""
        ...

    # An implementation MAY also offer `owner_connected(state) -> bool`, which
    # the manager reads through `getattr` to decide whether a steer needs a
    # revive first. It is deliberately NOT a member here: only the daemon's
    # coordinator-backed client holds the registry that can answer, and adding
    # a default-bodied method to a Protocol makes every structural implementer
    # depend on inheriting it. Absent means "assume connected", so a client
    # that cannot answer never causes a restart.


class ChannelSteerClient:
    """Deliver over the Grove channel receiver — the PANELESS backend's client.

    Reads the channel server's published ``{port, token}`` endpoint file and
    POSTs the message to its loopback receiver, which emits it into the running
    session as a ``notifications/claude/channel`` delivery. No endpoint
    (channels off, or the server not up) ⇒ a logged no-op — never a raise.

    A channel carries text and nothing else, so ``interrupt`` and ``set_model``
    refuse with ``SteeringUnsupported`` rather than pretending: the stream-json
    control channel that CAN do both is what a native workspace holds instead.
    """

    def send_message(self, state: WorkspaceState, text: str) -> None:
        session_id = self._session_id(state)
        endpoint = self._endpoint()
        if endpoint is None:
            logger.debug(
                "native steer: no channel endpoint for session {}; delivery dropped "
                "(is cfg.channels.enabled?)",
                session_id,
            )
            return
        import httpx  # noqa: PLC0415 — deferred: httpx is only needed on an actual delivery

        body = {"content": text, "meta": {"session_id": session_id}, "sender": _SENDER}
        try:
            httpx.post(
                f"http://127.0.0.1:{endpoint.port}{channel.RECEIVE_ROUTE}",
                json=body,
                headers={"Authorization": f"Bearer {endpoint.token}"},
                timeout=_DELIVER_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:  # best-effort: never raise into the caller
            logger.debug("native steer delivery failed for session {}: {}", session_id, exc)

    def interrupt(self, state: WorkspaceState) -> None:
        self._session_id(state)
        raise SteeringUnsupported(
            "the channel receiver carries messages only; interrupt needs a native session"
        )

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self._session_id(state)
        raise SteeringUnsupported(
            f"the channel receiver carries messages only; switching to {model!r} "
            "needs a native session"
        )

    def compact(self, state: WorkspaceState) -> None:
        self._session_id(state)
        raise SteeringUnsupported(
            "the channel receiver carries messages only; compaction needs a native session"
        )

    def invoke_control(self, state: WorkspaceState, name: str) -> None:
        del name
        self._session_id(state)
        raise SteeringUnsupported(
            "the channel receiver carries messages only; commands need a native session"
        )

    def answer(self, state: WorkspaceState, plan: str) -> None:
        del plan
        self._session_id(state)
        raise SteeringUnsupported(
            "the channel receiver carries messages only; answering a native ask "
            "needs a native session"
        )

    @staticmethod
    def _session_id(state: WorkspaceState) -> str:
        if not state.agent_session_id:
            raise AgentSessionNotFound(
                f"workspace {state.id} has no recorded agent session to steer natively"
            )
        return state.agent_session_id

    @staticmethod
    def _endpoint() -> channel.ChannelEndpoint | None:
        """Read the channel server's published discovery record, or ``None``."""
        import json  # noqa: PLC0415

        path = channel.channel_endpoint_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return channel.ChannelEndpoint.from_json(data)


class DaemonSteerClient:
    """Reach a native workspace's owner through the daemon — the OUT-OF-PROCESS client.

    The owner worker is connected to the daemon's coordinator and nowhere else,
    so a manager built by the CLI or TUI has no in-process road to it; it POSTs
    the daemon's own steer routes (``/message``, ``/interrupt``,
    ``/controls/model``), whose handlers run the same manager verb with the
    daemon's coordinator client injected. The bearer is a same-host session
    minted off the shared ``auth.json`` — the identical rendezvous the client
    SDK's local backend uses, which is why ``grove.client`` is not imported
    (it would invert the dependency direction).
    """

    def __init__(self, daemon_url: str) -> None:
        self._daemon_url = daemon_url.rstrip("/")
        self._token: str | None = None

    def send_message(self, state: WorkspaceState, text: str) -> None:
        self._post(state, "message", {"text": text})

    def interrupt(self, state: WorkspaceState) -> None:
        self._post(state, "interrupt", None)

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self._post(state, "controls/model", {"model": model})

    def compact(self, state: WorkspaceState) -> None:
        self._post(state, "controls/invoke", {"name": "compact"})

    def invoke_control(self, state: WorkspaceState, name: str) -> None:
        self._post(state, "controls/invoke", {"name": name})

    def answer(self, state: WorkspaceState, plan: str) -> None:
        # The daemon's answer route takes the wire shape, not the rendered
        # plan, and the manager that called us has already validated it; so
        # this arm re-posts the ORIGINAL request the caller holds. Reaching
        # here means a CLI/TUI manager answered a native workspace's question —
        # the daemon route is the one road to the owner.
        request = json.loads(plan)
        self._post(
            state,
            "question-answer",
            {
                "session_id": request["session_id"],
                "tool_use_id": request["tool_use_id"],
                "answers": [
                    {"selected_indexes": a["indexes"], "text": a.get("text")}
                    for a in request["answers"]
                ],
            },
        )

    def _post(self, state: WorkspaceState, verb: str, body: dict[str, Any] | None) -> None:
        import httpx  # noqa: PLC0415 — deferred, as above

        try:
            response = httpx.post(
                f"{self._daemon_url}/workspaces/{state.id}/{verb}",
                json=body,
                headers={"Authorization": f"Bearer {self._bearer()}"},
                timeout=_DELIVER_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise SteeringUnsupported(
                f"native session for workspace {state.id} is unreachable: the daemon at "
                f"{self._daemon_url} did not answer ({exc.__class__.__name__})"
            ) from exc
        if response.status_code >= 400:
            detail = response.json().get("detail", {}) if response.content else {}
            message = detail.get("message", response.text) if isinstance(detail, dict) else ""
            raise SteeringUnsupported(
                f"daemon refused native {verb} for workspace {state.id}: {message}"
            )

    def _bearer(self) -> str:
        """One same-host session per client instance, minted on first use.

        The same `auth.json` rendezvous `grove.client`'s local backend uses:
        daemon and CLI share the file as the same UID, so a self-approved
        pairing is an ordinary local session rather than a bypass.
        """
        if self._token is None:
            from grove.core.auth import SessionStore  # noqa: PLC0415 — off the import path

            store = SessionStore()
            challenge = store.pair_init(label="local-native-steer")
            store.pair_approve(challenge.challenge_id)
            _, token = store.pair_poll(challenge.challenge_id)
            if token is None:  # pragma: no cover - approve-then-poll always mints
                raise SteeringUnsupported("could not mint a local daemon session")
            self._token = token
        return self._token


__all__ = [
    "ChannelSteerClient",
    "DaemonSteerClient",
    "NativeSteerClient",
]
