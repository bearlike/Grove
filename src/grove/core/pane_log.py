"""The lines a remote native worker printed, numbered, so a host can follow them.

One question: which lines does a follower still need? A native workspace's pane
is the worker's own output (its frame log and the agent's prose), and a REMOTE
worker's pane is not on this host, so the worker keeps its recent lines here and
posts them to the daemon while someone watches; the daemon keeps a replica in
the same shape and serves it to the Stream tab. One class is both halves, so the
two cannot disagree about numbering or bounds.

Lines are numbered from the first one ever printed. ``end`` is one past the
newest line, which is the whole cursor: a batch is the next lines when it starts
at the replica's ``end``, and anything else is a gap the producer answers by
resending everything it still holds.
"""

from __future__ import annotations

from collections import deque
from typing import Final


class PaneGap(Exception):
    """A batch that does not continue the replica; ``end`` is where the replica stops."""

    def __init__(self, end: int) -> None:
        super().__init__(f"pane batch does not start at line {end}")
        self.end = end


class PaneLog:
    """The newest :attr:`LIMIT` lines of one worker's output, numbered from the first."""

    #: The scrollback a host pane keeps by default (``tmux.peek_history_lines``).
    LIMIT: Final = 500
    #: Frame lines are already cut at 400 characters; this bounds prose lines.
    LINE_CHARS: Final = 4000

    def __init__(self) -> None:
        self._lines: deque[str] = deque(maxlen=self.LIMIT)
        self._end = 0

    @property
    def end(self) -> int:
        """One past the newest line's number; ``0`` before anything was printed."""
        return self._end

    @property
    def start(self) -> int:
        """The number of the oldest line still held."""
        return self._end - len(self._lines)

    def append(self, text: str) -> None:
        """Record one printed block, one entry per line, each cut at :attr:`LINE_CHARS`."""
        for line in text.splitlines() or [""]:
            self._lines.append(line[: self.LINE_CHARS])
            self._end += 1

    def since(self, line: int) -> tuple[int, list[str]]:
        """``(first, lines)`` from ``line`` on, or everything held when ``line`` is gone.

        Args:
            line: the first line the follower does not have yet.

        Returns:
            The number of the first returned line and the lines themselves. A
            ``line`` older than :attr:`start` returns everything from
            :attr:`start`, which the caller recognises because ``first`` differs.
        """
        first = max(line, self.start)
        return first, list(self._lines)[first - self.start :]

    def apply(self, first: int, lines: list[str], *, reset: bool) -> None:
        """Take a producer's batch: the next lines, or (``reset``) its whole log.

        Raises:
            PaneGap: ``first`` is not :attr:`end` and the batch is not a reset.
        """
        if reset:
            self._lines.clear()
            self._end = first
        elif first != self._end:
            raise PaneGap(self._end)
        for line in lines:
            self._lines.append(line[: self.LINE_CHARS])
            self._end += 1

    def text(self) -> str | None:
        """The held lines as one pane capture, or ``None`` when nothing was printed."""
        return "\n".join(self._lines) if self._lines else None


__all__ = ["PaneGap", "PaneLog"]
