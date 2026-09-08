"""The native owner keeps execution and approvals; Grove observes submission only."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

#: One protocol frame as it crosses the owner's stdio, in either direction.
#: ``"send"`` is a frame the owner wrote to the provider, ``"recv"`` one it
#: read. The owner reports every frame it parses or writes and interprets
#: none of them here: the pane is the wire log, and a log that skipped the
#: frames the owner did not understand would hide exactly the ones a person
#: debugging a session needs to see.
FrameTrace = Callable[[Literal["send", "recv"], Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class NativeSubmission:
    stage: Literal["queued", "delivered", "unknown", "rejected"]
    evidence: str | None = None
    reason: (
        Literal["recipient_blocked", "busy_conflict", "transport_unknown", "backpressure"] | None
    ) = None


@dataclass(frozen=True, slots=True)
class NativeAnswer:
    """One human answer, per question in the captured batch's order.

    ``indexes`` name the chosen options (0-based); ``text`` is anything typed.
    The owner renders these into the provider's own answer shape — Claude's
    ``updatedInput.answers`` map, Codex's ``answers`` map or an approval
    decision — so the daemon never learns either wire format.
    """

    indexes: tuple[int, ...] = ()
    text: str | None = None


class NativeOwner(Protocol):
    """One explicitly launched native owner, never an attachment to a foreign TUI.

    ``interrupt`` and ``set_model`` answer ``True`` when the provider
    acknowledged the control and ``False`` when it refused or nothing was
    running to act on it; a transport that has gone away raises like ``send``.
    ``answer`` resolves the standing ask whose ``tool_use_id`` matches, and
    answers ``False`` when none does.
    """

    async def start(self, initial_prompt: str) -> str:
        """Return the exact provider session identity after native initialization."""
        ...

    async def send(self, message_id: str, text: str) -> NativeSubmission: ...

    async def interrupt(self) -> bool: ...

    async def set_model(self, model: str) -> bool: ...

    async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool: ...

    async def wait_closed(self) -> None:
        """Return on reader EOF or raise its failure; cancelling a watcher leaves it running."""
        ...

    async def close(self) -> None: ...


class AskRecorder:
    """Publish a native session's standing ask — and its stream facts — through the hook SPOOL.

    Claude Code's hooks do not fire for a headless (``-p``) session's
    ``AskUserQuestion`` — the ask reaches the owning client as a
    ``can_use_tool`` control request — and Codex has no hooks at all, so the
    owner is the only process that sees the question. It drops the ask into the
    same spool a containerized hook uses (``*.ask.json``), and the host folds it
    into the session's sidecar as the ``PendingQuestion`` the activity service
    and ``answer_question`` already read. One mechanism for a host and a
    container worker: the spool is bind-mounted at its own host path, so the
    worker never needs the sidecar directory or the daemon. The session id is
    bound after ``start`` because that is when the provider reports it.
    """

    SUFFIX = ".ask.json"
    FACTS_SUFFIX = ".facts.json"

    def __init__(self, spool_dir: Path) -> None:
        self._spool_dir = spool_dir
        self._session_id: str | None = None

    def bind(self, session_id: str) -> None:
        self._session_id = session_id

    def asked(self, tool_use_id: str, tool_name: str, tool_input: dict[str, Any]) -> None:
        self._drop(
            {
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "asked_at": datetime.now(UTC).isoformat(),
            }
        )

    def resolved(self) -> None:
        self._drop(None)

    def facts(self, **stated: object) -> None:
        """Publish the stream facts one frame stated (``*.facts.json``).

        Only the keyword arguments given are stated; the host merges them over
        the sidecar's standing facts field by field, so a ``result`` frame's
        cost never blanks an exit code the previous ``item/completed`` set.
        """
        self._drop({k: v for k, v in stated.items() if v is not None}, suffix=self.FACTS_SUFFIX)

    def _drop(self, body: dict[str, Any] | None, *, suffix: str | None = None) -> None:
        if self._session_id is None:
            return
        key = "facts" if suffix == self.FACTS_SUFFIX else "question"
        payload = {"session_id": self._session_id, key: body}
        try:
            self._spool_dir.mkdir(parents=True, exist_ok=True)
            target = self._spool_dir / f"{uuid4().hex}{suffix or self.SUFFIX}"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(target)
        except OSError:
            # Best-effort like every hook write: a lost ask costs a badge, and
            # the answer path re-reads the owner's own held request anyway.
            return
