"""Native steering — deliver a message/interrupt to a paneless agent (#172).

The tmux backend types steering into a live pane (``tmux.send_text`` /
``send_keys``); a **paneless** runtime (``provides_pane = False``, #146 — a
headless detached process) has no pane, so steering must ride the agent's own
native input channel instead. This module is that delivery seam: the
:class:`NativeSteerClient` Protocol and its default :class:`ChannelSteerClient`,
which forwards a message over the #182 Grove **channel** receiver (Claude Code's
native "act on this message" transport).

It is the paneless twin of ``grove.core.mewbo``'s remote steering: the manager's
``_steer_native`` is the ``_steer_remote`` mirror, injected the same way
(``mewbo_client`` ↔ ``native_steer``) so a test drives it with a fake and no
socket. Best-effort by contract — a delivery failure logs and returns; steering
must never raise into the caller's hot path (the render loop, the daemon route).

Concrete-transport status: message delivery rides the channel receiver (real when
``cfg.channels.enabled`` spawned the server, a logged no-op otherwise, exactly as
#182 left its daemon-side plumbing a deferred seam). A native *interrupt* has no
channel primitive yet — the stream-json/SDK ``control_request`` ``interrupt`` is
the eventual home — so it is a best-effort logged no-op today, which is still the
#172 win over the old ``CapabilityUnavailable`` raise. Dependencies flow inward —
this imports ``channel`` (its public receiver seams) and the agent model, never
the manager.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from loguru import logger

from grove.core import channel
from grove.core.agents.model import AgentQuestion, AnswerSelection

# The loopback receiver POST is same-host and near-instant; a tight timeout keeps
# a stalled channel server from ever blocking a steering call.
_DELIVER_TIMEOUT_SECONDS = 5.0

# Grove's declared sender identity on a native delivery (matches the channel
# allowlist's expectations; empty allowlist accepts it regardless).
_SENDER = "grove"


class NativeSteerClient(Protocol):
    """Deliver steering to a paneless agent over its native channel (#172).

    Two ops, mirroring the mewbo remote surface ``_steer_remote`` dispatches to
    (``send_message`` / ``interrupt``), keyed by the workspace's agent session id.
    Every method is best-effort: it logs and returns on failure, never raises —
    steering a paneless runtime is a fire-and-forget delivery, not a transaction.
    """

    def send_message(self, session_id: str, text: str) -> None: ...

    def interrupt(self, session_id: str) -> None: ...


class ChannelSteerClient:
    """Default :class:`NativeSteerClient` — deliver over the #182 channel receiver.

    Reads the channel server's published ``{port, token}`` endpoint file and POSTs
    the message to its loopback receiver, which emits it into the running session
    as a ``notifications/claude/channel`` delivery. No endpoint (channels off, or
    the server not up) ⇒ a logged no-op — never a raise.
    """

    def send_message(self, session_id: str, text: str) -> None:
        endpoint = self._endpoint()
        if endpoint is None:
            logger.debug(
                "native steer: no channel endpoint for session {}; delivery dropped "
                "(is cfg.channels.enabled?)",
                session_id,
            )
            return
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

    def interrupt(self, session_id: str) -> None:
        # No channel-protocol interrupt primitive exists yet; the stream-json /
        # SDK `control_request` interrupt is the eventual home. Best-effort no-op
        # so the manager's paneless interrupt no longer raises (the #172 contract).
        logger.debug(
            "native interrupt for session {} is not yet a channel primitive; "
            "deferred to the stream-json control channel",
            session_id,
        )

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


def render_answer(questions: tuple[AgentQuestion, ...], selections: list[AnswerSelection]) -> str:
    """Render a captured question batch + its answers into a deliverable message.

    The native counterpart of ``ClaudeCodeAdapter.build_answer_keys``: the tmux
    path drives the picker by keystroke, but a channel delivers plain text the
    running session reads, so an answer becomes a human-readable line per question
    — the chosen option labels, or the free text. Pure and defensive: an index out
    of range is skipped rather than raised, an empty result degrades to a bare
    acknowledgement (never an empty delivery). This is shape rendering, not model
    semantics — it forwards the human's choice verbatim.
    """
    lines: list[str] = []
    for i, selection in enumerate(selections):
        question = questions[i] if i < len(questions) else None
        if selection.text is not None:
            answer = selection.text
        elif question is not None:
            labels = [
                question.options[idx].label
                for idx in selection.indexes
                if 0 <= idx < len(question.options)
            ]
            answer = ", ".join(labels)
        else:
            answer = ""
        if question is not None and question.prompt:
            lines.append(f"{question.prompt.strip()} {answer}".strip())
        elif answer:
            lines.append(answer)
    return "\n".join(lines) if lines else "(answer submitted)"


__all__ = [
    "ChannelSteerClient",
    "NativeSteerClient",
    "render_answer",
]
