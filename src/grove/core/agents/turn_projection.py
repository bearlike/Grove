"""Incremental turn projection over an append-mostly message spine.

``read_messages`` folds a transcript incrementally; the turn projection above it
did not. It hung off a stat-signature memo, so every appended byte invalidated
the whole product and the renderer walked every historical message again —
measured on a 39.9 MB transcript at ~300-377 ms per one-line append, which a
live session pays forever.

The fix is the shape the spine already has. A new record can only extend the
turn currently open or start a new one, so every earlier turn is frozen
(measured: 6 of 7 turns byte-identical over 45 s). This class keeps those frozen
turns BY REFERENCE and re-renders only from the last turn boundary, which makes
the steady-state append O(delta) instead of O(history).

Two things reach backwards and both are handled explicitly rather than assumed
away:

- **A late ``tool_result`` resolving a call inside a frozen turn.** Outcomes are
  forward references, so a call can settle arbitrarily later. The ids left open
  at each boundary are tracked, and a result naming one forces a full rebuild.
  It is rare (3 running calls of 7,868 real Claude calls) and correctness
  critical, so it is paid whole rather than approximated.
- **Fold state that spans turns**, i.e. Claude's ``TaskBoard``: a ``TaskUpdate``
  in the open turn can name a ``TaskCreate`` dozens of turns back. The carry is
  advanced exactly once per closed turn and FORKED for the open one, so the
  reader's repeated re-render of a live turn can never double-apply into it.

The prefix is validated by object identity rather than trusted, because a
split-block continuation and a late compaction summary both REPLACE an
already-published message. That scan is a pointer comparison per message — the
same order as the tuple rebuild the folder just performed, and nothing like the
per-message entry construction it replaces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from grove.core.agents.model import AgentMessage, SessionTurn


def window_turns(turns: tuple[SessionTurn, ...], last: int | None) -> tuple[SessionTurn, ...]:
    """Apply a caller's ``last`` window to a COMPLETE turn tuple.

    ``last`` is a slice at the adapter boundary and never part of a cache key:
    two clients asking for different windows read one projection, and
    ``total_turns`` stays honest because the folder always holds the whole list.
    """
    if last is None:
        return turns
    return tuple(turns[-last:]) if last > 0 else ()


class TurnProjection[CarryT]:
    """A folder-owned turn tuple advanced by re-rendering only the open turn.

    ``render`` is the adapter's ONE turn builder — this class changes when it
    runs, never what it produces. ``starts_turn`` must be the same predicate
    that builder uses to cut a turn, or a frozen prefix would not correspond to
    any turn the builder would emit; both adapters expose it from the builder's
    own class for exactly that reason. ``fork_carry`` produces a fresh carry
    from ``None`` and an independent copy otherwise.
    """

    __slots__ = (
        "_boundary",
        "_carry",
        "_fork",
        "_frozen",
        "_messages",
        "_open_calls",
        "_render",
        "_starts_turn",
        "_turns",
    )

    def __init__(
        self,
        *,
        render: Callable[[Sequence[AgentMessage], CarryT], tuple[SessionTurn, ...]],
        starts_turn: Callable[[AgentMessage], bool],
        fork_carry: Callable[[CarryT | None], CarryT],
    ) -> None:
        self._render = render
        self._starts_turn = starts_turn
        self._fork = fork_carry
        self._messages: tuple[AgentMessage, ...] = ()
        self._turns: tuple[SessionTurn, ...] = ()
        self._frozen: tuple[SessionTurn, ...] = ()
        self._boundary = 0
        self._carry: CarryT = fork_carry(None)
        self._open_calls: set[str] = set()

    def turns(self, messages: tuple[AgentMessage, ...]) -> tuple[SessionTurn, ...]:
        """The complete turn tuple for ``messages``, oldest first."""
        if messages is self._messages:
            # The folder republishes the same tuple object while it is clean, so
            # an unchanged transcript costs nothing beyond this identity test.
            return self._turns
        if not self._reusable(messages):
            self._reset()
        return self._advance(messages)

    # ── internal ──────────────────────────────────────────────────────────

    def _reusable(self, messages: tuple[AgentMessage, ...]) -> bool:
        """Whether the frozen prefix still describes ``messages``.

        Nothing is frozen at boundary zero, so there is nothing to invalidate.
        """
        boundary = self._boundary
        if boundary == 0:
            return True
        previous = self._messages
        if len(messages) < boundary or len(previous) < boundary:
            return False
        if any(messages[index] is not previous[index] for index in range(boundary)):
            # A replaced record (a split-block continuation, a late compaction
            # summary) rewrote published history; a broader rebuild is the
            # honest answer, and these are not the steady-state append.
            return False
        return not self._resolves_frozen(messages, boundary)

    def _resolves_frozen(self, messages: tuple[AgentMessage, ...], boundary: int) -> bool:
        """Whether anything past ``boundary`` settles a call left open before it.

        Membership decides, never truthiness: a tool that returned nothing is a
        RESOLVED call, and rendering it as still running is the bug this guard
        exists to prevent.
        """
        if not self._open_calls:
            return False
        for message in messages[boundary:]:
            for block in message.content:
                if block.type == "tool_result" and block.tool_use_id in self._open_calls:
                    return True
        return False

    def _advance(self, messages: tuple[AgentMessage, ...]) -> tuple[SessionTurn, ...]:
        start = self._boundary
        boundary = self._last_turn_start(messages, start)
        if boundary > start and self._freeze_would_strand(messages, start, boundary):
            # A span about to be frozen holds a call whose result is already in
            # the tail. Freezing it would render that call as still running, so
            # the span stays open and this read pays a wider render once.
            boundary = start
        frozen = self._frozen
        carry = self._carry
        if boundary > start:
            closing = messages[start:boundary]
            # Rendering the closed span ADVANCES the persistent carry in place,
            # so it describes the state exactly at the new boundary.
            frozen = frozen + self._render(closing, carry)
            self._track_open_calls(closing)
        tail = self._render(messages[boundary:], self._fork(carry))
        self._boundary = boundary
        self._frozen = frozen
        self._carry = carry
        self._messages = messages
        self._turns = frozen + tail
        return self._turns

    def _last_turn_start(self, messages: tuple[AgentMessage, ...], start: int) -> int:
        """The newest turn boundary at or after ``start``; ``start`` if none.

        Scanning backwards from the end keeps this O(delta): the answer is
        almost always within the records just appended.
        """
        for index in range(len(messages) - 1, start - 1, -1):
            if self._starts_turn(messages[index]):
                return index
        return start

    @staticmethod
    def _freeze_would_strand(messages: tuple[AgentMessage, ...], start: int, boundary: int) -> bool:
        """Whether freezing ``start:boundary`` buries a call the tail resolves.

        The reverse of :meth:`_resolves_frozen` in time: that one catches a
        result arriving after its call was frozen, this one refuses to freeze a
        call whose result has ALREADY landed in the same read.
        """
        opened: set[str] = set()
        for message in messages[start:boundary]:
            for block in message.content:
                if not block.tool_use_id:
                    continue
                if block.type == "tool_use":
                    opened.add(block.tool_use_id)
                elif block.type == "tool_result":
                    opened.discard(block.tool_use_id)
        if not opened:
            return False
        return any(
            block.type == "tool_result" and block.tool_use_id in opened
            for message in messages[boundary:]
            for block in message.content
        )

    def _track_open_calls(self, span: Sequence[AgentMessage]) -> None:
        """Remember the calls a newly frozen span left unresolved."""
        for message in span:
            for block in message.content:
                if not block.tool_use_id:
                    continue
                if block.type == "tool_use":
                    self._open_calls.add(block.tool_use_id)
                elif block.type == "tool_result":
                    self._open_calls.discard(block.tool_use_id)

    def _reset(self) -> None:
        self._messages = ()
        self._turns = ()
        self._frozen = ()
        self._boundary = 0
        self._carry = self._fork(None)
        self._open_calls = set()
