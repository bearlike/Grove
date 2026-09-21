"""How a session's turn list is windowed for one response.

Its own module because TWO routers need it — the authenticated
``/workspaces/{id}/sessions/{sid}/turns`` in ``app.py`` and the unauthenticated
``/public/{token}/turns`` in ``_public.py`` — and ``_public`` cannot import
``app`` without closing a cycle. One definition, so the public follower and the
private one cannot disagree about what a cursor means.
"""

from __future__ import annotations

from typing import NamedTuple

from grove.core.agents import SessionTurn


class TurnWindow(NamedTuple):
    """Which slice of a session's turns one response carries, and why."""

    turns: tuple[SessionTurn, ...]
    total: int
    first_index: int
    incremental: bool


def turn_window(
    turns: tuple[SessionTurn, ...],
    *,
    last: int | None,
    after_turn: int | None,
    before_turn: int | None = None,
) -> TurnWindow:
    """Resolve the requested window over a session's complete turn list.

    ``after_turn`` is INCLUSIVE of its own index, and that is the whole answer to
    the tail-mutation problem: turns are append-*mostly*, not append-only — the
    last turn keeps growing as the agent streams parts and resolves tool calls,
    while every earlier one is frozen (measured on a live session: 6 of 7 turns
    byte-identical over 45 s, only the tail moved). An exclusive cursor would
    freeze a half-finished turn on screen for the rest of the session, so the
    client's last-known turn is always re-sent and it replaces from
    ``first_index`` rather than blindly appending.

    A cursor STRICTLY BEYOND the end is the GAP: the session now holds fewer
    turns than the client claims to have seen, so the transcript was replaced
    or forked under the reader, ordinals no longer mean what the client thinks,
    and the honest answer is the whole session with ``incremental=False``.
    Fail-safe by construction — anything this cannot prove it can serve
    incrementally comes back whole, mirroring ``_SseHub.can_replay``'s fall
    back to a full snapshot.

    ``after_turn == total`` is deliberately NOT a gap but an empty incremental
    window: the client is exactly up to date, and answering a one-off-by-one
    cursor with the entire session would make the common "nothing happened"
    tick the most expensive request on the route.

    ``before_turn`` is the mirror of ``after_turn`` and the reason "load
    earlier" stops re-downloading the tail. It is EXCLUSIVE of its own index —
    the client already holds that turn, and unlike the forward case it is a
    FROZEN one (only the tail mutates), so re-sending it would buy nothing. With
    ``last`` it names a page size (``turns[n - last : n]``); without one it means
    every turn before ``n``. ``incremental`` stays False: this window is placed
    by its own ``first_turn_index`` against a prefix, not resumed from a cursor,
    and the client PREPENDS rather than replaces.

    A ``before_turn`` past the end CLAMPS to ``total`` rather than falling back
    to the whole session the way an over-run ``after_turn`` does, because the
    two make different claims. A forward cursor asserts "I have seen turn n", so
    a transcript shorter than that has been replaced under the reader. A
    backward one only asks for history preceding an endpoint, and an endpoint
    beyond the end is satisfied by everything there is.
    """
    total = len(turns)
    if before_turn is not None:
        end = min(before_turn, total)
        start = max(end - last, 0) if last is not None else 0
        return TurnWindow(turns[start:end], total, start, False)
    if after_turn is not None:
        if after_turn > total:
            return TurnWindow(turns, total, 0, False)
        return TurnWindow(turns[after_turn:], total, after_turn, True)
    if last is not None:
        start = max(total - last, 0)
        return TurnWindow(turns[start:], total, start, False)
    return TurnWindow(turns, total, 0, False)
