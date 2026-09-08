"""DiagramFiles validation, format normalization, and safe file boundary."""

from __future__ import annotations

import base64
import stat
import urllib.parse
import zlib
from pathlib import Path

import pytest

from grove.core.diagrams import MAX_DIAGRAM_BYTES, DiagramFiles
from grove.core.errors import DiagramUnavailable, WorkspaceStateError


@pytest.fixture
def diagram(tmp_path: Path) -> tuple[Path, Path]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    source = worktree / "architecture.drawio"
    source.write_text(
        '<mxfile><diagram id="p">'
        '&lt;mxGraphModel&gt;&lt;root&gt;&lt;mxCell id="0"/&gt;'
        '&lt;mxCell id="1" parent="0"/&gt;&lt;/root&gt;&lt;/mxGraphModel&gt;'
        "</diagram></mxfile>"
    )
    return worktree, source


def test_read_escaped_page_is_expanded_without_changing_revision(
    diagram: tuple[Path, Path],
) -> None:
    worktree, source = diagram
    expected_revision = DiagramFiles._revision(source.read_bytes())
    files = DiagramFiles()

    with files.locked(worktree, "architecture.drawio") as target:
        document = files.contents(target)

    assert "<mxGraphModel>" in document.xml
    assert "&lt;mxGraphModel" not in document.xml
    assert document.revision == expected_revision
    assert len(document.revision) == 64


def test_compressed_page_is_expanded_without_writing_source(diagram: tuple[Path, Path]) -> None:
    worktree, source = diagram
    page = urllib.parse.quote(
        '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel>',
        safe="~()*!.'",
    )
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(page.encode()) + compressor.flush()
    encoded = urllib.parse.quote(base64.b64encode(compressed).decode(), safe="")
    raw = f'<mxfile><diagram id="p">{encoded}</diagram></mxfile>'
    source.write_text(raw)
    files = DiagramFiles()

    with files.locked(worktree, "architecture.drawio") as target:
        document = files.contents(target)

    assert "<mxGraphModel>" in document.xml
    assert "&lt;mxGraphModel" not in document.xml
    assert source.read_text() == raw


def test_replace_preserves_existing_file_mode(diagram: tuple[Path, Path]) -> None:
    worktree, source = diagram
    source.chmod(0o640)
    files = DiagramFiles()
    with files.locked(worktree, "architecture.drawio") as target:
        current = files.contents(target)
        files.replace(
            target,
            expected_revision=current.revision,
            xml=(
                '<mxfile><diagram id="next"><mxGraphModel><root>'
                '<mxCell id="0"/><mxCell id="1" parent="0"/>'
                "</root></mxGraphModel></diagram></mxfile>"
            ),
        )

    assert stat.S_IMODE(source.stat().st_mode) == 0o640


def test_rejects_target_and_parent_symlinks(diagram: tuple[Path, Path], tmp_path: Path) -> None:
    worktree, source = diagram
    outside = tmp_path / "outside.drawio"
    outside.write_text("<mxfile><diagram/></mxfile>")
    source.unlink()
    source.symlink_to(outside)
    with (
        pytest.raises(WorkspaceStateError),
        DiagramFiles().locked(worktree, "architecture.drawio"),
    ):
        pass

    source.unlink()
    (worktree / "nested").symlink_to(tmp_path)
    with (
        pytest.raises(WorkspaceStateError),
        DiagramFiles().locked(worktree, "nested/outside.drawio"),
    ):
        pass


def test_rejects_dtd_and_decompression_bomb(diagram: tuple[Path, Path]) -> None:
    worktree, source = diagram
    files = DiagramFiles()
    source.write_text('<!DOCTYPE mxfile [<!ENTITY x "x">]><mxfile><diagram>&x;</diagram></mxfile>')
    with (
        pytest.raises(WorkspaceStateError),
        files.locked(worktree, "architecture.drawio") as target,
    ):
        files.contents(target)

    large_page = (
        '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0" value="'
        + "x" * (MAX_DIAGRAM_BYTES + 1)
        + '"/></root></mxGraphModel>'
    )
    payload = urllib.parse.quote(large_page, safe="~()*!.'")
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload.encode()) + compressor.flush()
    source.write_text(
        "<mxfile><diagram>"
        + urllib.parse.quote(base64.b64encode(compressed).decode(), safe="")
        + "</diagram></mxfile>"
    )
    with (
        pytest.raises(WorkspaceStateError),
        files.locked(worktree, "architecture.drawio") as target,
    ):
        files.contents(target)


def test_missing_worktree_and_file_are_unavailable(diagram: tuple[Path, Path]) -> None:
    worktree, source = diagram
    files = DiagramFiles()
    source.unlink()
    with pytest.raises(DiagramUnavailable), files.locked(worktree, "architecture.drawio"):
        pass
    worktree.rmdir()
    with pytest.raises(DiagramUnavailable), files.locked(worktree, "architecture.drawio"):
        pass


def test_private_lock_symlink_is_refused(
    diagram: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, source = diagram
    lock_root = tmp_path / "diagram-locks"
    target = worktree / "architecture.drawio"
    lock = lock_root / DiagramFiles._revision(str(target).encode())
    lock_root.mkdir()
    lock.symlink_to(tmp_path / "elsewhere")
    monkeypatch.setattr("grove.core.paths.user_state_path", lambda: tmp_path / "state.json")

    with (
        pytest.raises(WorkspaceStateError),
        DiagramFiles().locked(worktree, "architecture.drawio"),
    ):
        pass

    assert source.exists()


def test_private_lock_rejects_hard_link(
    diagram: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, _source = diagram
    lock_root = tmp_path / "diagram-locks"
    target = worktree / "architecture.drawio"
    lock = lock_root / DiagramFiles._revision(str(target).encode())
    lock_root.mkdir()
    lock.touch()
    (lock_root / "linked-lock").hardlink_to(lock)
    monkeypatch.setattr("grove.core.paths.user_state_path", lambda: tmp_path / "state.json")

    with (
        pytest.raises(WorkspaceStateError),
        DiagramFiles().locked(worktree, "architecture.drawio"),
    ):
        pass
