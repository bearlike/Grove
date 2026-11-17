"""The host side of remote transcript replication: the file size is the cursor (#1099).

Two properties carry the whole protocol and both are pinned here. A chunk is
applied only at the file's current size, and any other offset is answered with
that size rather than written, so a replay after a reconnect can never
duplicate or shadow bytes. And the path a worker names can never place a file
outside the workspace's own root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.transcript_mirror import BadRelativePath, ChunkRefused, TranscriptMirror


def test_consecutive_chunks_append_and_the_size_is_the_cursor(tmp_path: Path) -> None:
    mirror = TranscriptMirror(tmp_path)
    first = mirror.append("projects/x/s.jsonl", 0, b'{"a":1}\n')
    second = mirror.append("projects/x/s.jsonl", first.size, b'{"b":2}\n')
    assert (second.path.read_bytes(), second.size) == (b'{"a":1}\n{"b":2}\n', 16)
    assert mirror.size_of("projects/x/s.jsonl") == 16


def test_a_replayed_or_skipped_chunk_is_refused_with_the_real_size(tmp_path: Path) -> None:
    """Delete the offset check and the replay below APPENDS a second copy."""
    mirror = TranscriptMirror(tmp_path)
    mirror.append("s.jsonl", 0, b"abc")
    with pytest.raises(ChunkRefused) as behind:
        mirror.append("s.jsonl", 0, b"abc")
    with pytest.raises(ChunkRefused) as ahead:
        mirror.append("s.jsonl", 9, b"xyz")
    assert (behind.value.size, ahead.value.size) == (3, 3)
    assert (tmp_path / "s.jsonl").read_bytes() == b"abc"


def test_a_file_nobody_wrote_yet_has_size_zero(tmp_path: Path) -> None:
    assert TranscriptMirror(tmp_path).size_of("never/there.jsonl") == 0


@pytest.mark.parametrize(
    "relative",
    ["", "/etc/passwd", "../outside.jsonl", "a/../../b", "a\\b", "x/../../y"],
)
def test_a_path_that_could_escape_the_root_is_refused(tmp_path: Path, relative: str) -> None:
    mirror = TranscriptMirror(tmp_path)
    with pytest.raises(BadRelativePath):
        mirror.append(relative, 0, b"x")
    assert not any(tmp_path.rglob("*")), "nothing may be written for a refused path"
