"""A remote worker's pane lines, numbered so a host replica can follow them (#1101)."""

from __future__ import annotations

import pytest

from grove.core.pane_log import PaneGap, PaneLog


def test_a_follower_gets_only_new_lines_then_everything_once_it_falls_behind() -> None:
    log = PaneLog()
    log.append("a\nb")
    assert log.since(0) == (0, ["a", "b"])
    log.append("c")
    assert log.since(2) == (2, ["c"])
    for n in range(PaneLog.LIMIT):
        log.append(str(n))
    first, lines = log.since(2)
    assert first == log.start > 2 and len(lines) == PaneLog.LIMIT


def test_the_replica_applies_only_the_next_lines_unless_reset() -> None:
    replica = PaneLog()
    replica.apply(0, ["a", "b"], reset=False)
    with pytest.raises(PaneGap) as gap:
        replica.apply(5, ["x"], reset=False)
    assert gap.value.end == 2
    replica.apply(40, ["x", "y"], reset=True)
    assert (replica.start, replica.end, replica.text()) == (40, 42, "x\ny")
