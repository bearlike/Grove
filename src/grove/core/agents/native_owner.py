"""The native owner keeps execution and approvals; Grove observes submission only."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from uuid import uuid4

from grove.core.agents.model import NativeOperation

#: The facts whose ``None`` is a statement, not an absence: the host sidecar
#: clears them on it (`hook._CLEARABLE_FACTS` reads the same keys back).
_CLEARED_BY_NONE: Final = frozenset({"context_state", "operation", "compact_error"})

#: One protocol frame as it crosses the owner's stdio, in either direction.
#: ``"send"`` is a frame the owner wrote to the provider, ``"recv"`` one it
#: read. The owner reports every frame it parses or writes and interprets
#: none of them here: the pane is the wire log, and a log that skipped the
#: frames the owner did not understand would hide exactly the ones a person
#: debugging a session needs to see.
FrameTrace = Callable[[Literal["send", "recv"], Mapping[str, Any]], None]

#: Where a remote worker's spool drops go instead of a directory: one
#: ``(suffix, payload)`` per drop, in the order they were made.
SpoolSink = Callable[[str, dict[str, Any]], None]


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

    async def set_effort(self, effort: str) -> bool:
        """Change the reasoning effort of every later turn, or refuse.

        ``True`` means the provider's control channel accepted the request.
        Claude acknowledges any string (measured 2.1.289: even ``bogus``), so
        the caller validates the level against the adapter's list first; an
        acknowledgement is delivery, never proof the level took effect.
        """
        ...

    async def compact(self) -> bool:
        """Request provider-native context compaction, or refuse when unsupported."""
        ...

    async def invoke_control(self, name: str) -> bool:
        """Invoke one provider-native named command, or refuse when unsupported."""
        ...

    async def run_command(self, command: str) -> bool:
        """Run one slash command and return once the provider says it FINISHED.

        ``True`` only when the provider reported the command completed;
        ``False`` when it was cancelled, the provider cannot report completion,
        or the transport is gone. A Grove command waits on this between steps,
        so a provider that cannot answer must refuse rather than guess.
        """
        ...

    @property
    def current_model(self) -> str | None:
        """The model the session reports it is on, in the ``--model`` vocabulary."""
        ...

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

    A REMOTE worker has no path into that spool, so it is built with a
    ``sink`` that carries each drop to the daemon instead, and only then does
    the recorder also state turn edges (:meth:`turn`): a host or container
    session's transcript is on this disk, where the poll already reads its turns.
    """

    SUFFIX = ".ask.json"
    FACTS_SUFFIX = ".facts.json"
    #: A hook-shaped turn edge (``UserPromptSubmit`` / ``Stop``), folded through
    #: the ordinary hook-event arm because its suffix names no other arm.
    TURN_SUFFIX = ".turn.json"
    #: The only events a turn drop may carry: a turn began, a turn ended.
    TURN_EVENTS: Final = frozenset({"UserPromptSubmit", "Stop"})

    def __init__(self, spool_dir: Path | None = None, *, sink: SpoolSink | None = None) -> None:
        """Publish into a local spool, or through a sink; exactly one is given.

        Args:
            spool_dir: the host hook spool, for a worker that shares this host's
                (or a bind-mounted) filesystem.
            sink: receives ``(suffix, payload)`` per drop, for a worker that
                cannot reach the spool; it must keep the drops in order.

        Raises:
            ValueError: neither or both were given.
        """
        if (spool_dir is None) == (sink is None):
            raise ValueError("an AskRecorder needs exactly one of spool_dir or sink")
        self._spool_dir = spool_dir
        self._sink = sink
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
        ``context_state`` is also retained when it explicitly clears a prior
        context — absence would mean an older producer that must invalidate it.
        ``operation`` and ``compact_error`` are set-or-clear rather than
        accumulated, so an explicit ``None`` for them is kept as a clear.
        """
        self._drop(
            {
                k: v.to_json() if isinstance(v, NativeOperation) else v
                for k, v in stated.items()
                if v is not None or k in _CLEARED_BY_NONE
            },
            suffix=self.FACTS_SUFFIX,
        )

    def turn(self, event: str) -> None:
        """State that a root turn began or ended; a no-op where the transcript is local.

        Args:
            event: ``UserPromptSubmit`` for a turn that began, ``Stop`` for one
                that ended, mapped to WORKING / WAITING by the hook's own table.
        """
        if self._sink is None or self._session_id is None or event not in self.TURN_EVENTS:
            return
        self._publish(self.TURN_SUFFIX, {"hook_event_name": event, "session_id": self._session_id})

    def _drop(self, body: dict[str, Any] | None, *, suffix: str | None = None) -> None:
        if self._session_id is None:
            return
        key = "facts" if suffix == self.FACTS_SUFFIX else "question"
        self._publish(suffix or self.SUFFIX, {"session_id": self._session_id, key: body})

    def _publish(self, suffix: str, payload: dict[str, Any]) -> None:
        if self._sink is not None:
            self._sink(suffix, payload)
        elif self._spool_dir is not None:
            self.write_spool(self._spool_dir, suffix, payload)

    @staticmethod
    def write_spool(spool_dir: Path, suffix: str, payload: dict[str, Any]) -> None:
        """Write one drop into ``spool_dir`` under a write-ordered name, best-effort.

        Args:
            spool_dir: the host hook spool the sidecar reader drains.
            suffix: which fold arm reads it (``SUFFIX``, ``FACTS_SUFFIX``,
                ``TURN_SUFFIX``).
            payload: the drop's JSON body, carrying its ``session_id``.
        """
        try:
            spool_dir.mkdir(parents=True, exist_ok=True)
            # The drain folds in (mtime, name) order, and mtimes COLLIDE: six
            # drops written back to back measured two distinct mtimes. A random
            # name then orders a step's start and its end by chance, and folding
            # them reversed leaves the step standing forever. A time-ordered
            # prefix makes the tie-break the write order.
            target = spool_dir / f"{time.time_ns():020d}-{uuid4().hex}{suffix}"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(target)
        except OSError:
            # Best-effort like every hook write: a lost ask costs a badge, and
            # the answer path re-reads the owner's own held request anyway.
            return
