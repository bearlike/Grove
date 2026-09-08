"""Message attachments: where a file lands, and how the agent is told to open it.

Two halves. ``AttachmentStore`` is pure path work — the sanitization that stops
a filename becoming a path, and the id check that stops a caller reading outside
the root. The manager half is the part with a real workspace under it: the git
exclude, the namespace translation, and the Grove-fenced block that ends up in
front of the agent.

The placement is the whole design and is asserted directly: attachments live
under the WORKTREE, because that is the only directory a host process and a
containerized agent both see. A host temp directory would be an address half the
fleet could not open.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.attachments import AttachmentStore
from grove.core.config import GroveConfig
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.conftest import FAKE_REMOTE_FOLDER, FakeTmux

FULL_ID = "d" * 64


# ─── the store: names and ids are untrusted input ───────────────────────────


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("/absolute/path/x.png", "x.png"),
        ("-rf", "rf"),
        (".hidden", "hidden"),
        ("my file (1).PNG", "my_file_1_.PNG"),
        ("   ", "attachment"),
        ("///", "attachment"),
    ],
)
def test_safe_name_reduces_a_filename_to_one_harmless_segment(given: str, expected: str) -> None:
    """A filename reaches a filesystem path, so it is never trusted as one.
    Traversal, absolute paths and a flag-shaped leading dash all collapse here —
    the value-becomes-syntax class, arriving through an upload form."""
    assert AttachmentStore.safe_name(given) == expected


def test_storing_keeps_the_extension_so_the_agent_can_tell_what_it_got(tmp_path: Path) -> None:
    stored = AttachmentStore.store(tmp_path, "diagram.png", b"\x89PNG")

    assert stored.name == "diagram.png"
    assert stored.path.read_bytes() == b"\x89PNG"
    assert stored.path.parent.parent == tmp_path / AttachmentStore.RELDIR


def test_two_files_with_the_same_name_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """A person attaches two screenshots in one session; a flat directory would
    silently serve the second for both."""
    first = AttachmentStore.store(tmp_path, "shot.png", b"one")
    second = AttachmentStore.store(tmp_path, "shot.png", b"two")

    assert first.id != second.id
    assert first.path.read_bytes() == b"one"
    assert second.path.read_bytes() == b"two"


def test_an_oversize_payload_is_refused_loudly(tmp_path: Path) -> None:
    """Not best-effort: an upload is a transaction, and a silent success that
    stores nothing leaves the agent pointed at a file that is not there."""
    with pytest.raises(ValueError, match="over the"):
        AttachmentStore.store(tmp_path, "big.bin", b"x" * (AttachmentStore.MAX_BYTES + 1))


def test_resolve_round_trips_a_stored_attachment(tmp_path: Path) -> None:
    stored = AttachmentStore.store(tmp_path, "notes.md", b"hello")

    found = AttachmentStore.resolve(tmp_path, stored.id)

    assert found is not None
    assert (found.name, found.path) == (stored.name, stored.path)


@pytest.mark.parametrize("bad_id", ["../../../etc", "..", "/etc", "abc", "ZZZZZZZZZZZZ", ""])
def test_resolve_refuses_an_id_that_is_not_one_before_touching_a_path(
    tmp_path: Path, bad_id: str
) -> None:
    """Containment by construction: the id is pattern-checked before it is ever
    joined, rather than joined and then checked with ``relative_to``."""
    assert AttachmentStore.resolve(tmp_path, bad_id) is None


def test_resolve_answers_none_for_an_id_nobody_stored(tmp_path: Path) -> None:
    assert AttachmentStore.resolve(tmp_path, "0123456789ab") is None


# ─── the manager: exclude, namespace, and the block the agent reads ─────────


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # installed via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


def _workspace(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="attach here"))


def test_an_upload_lands_under_the_worktree_and_reports_the_agent_s_path(
    manager: WorkspaceManager,
) -> None:
    state = _workspace(manager)

    view = manager.add_attachment(state.id, "diagram.png", b"\x89PNG")

    assert view.name == "diagram.png"
    assert view.path.startswith(str(Path(state.worktree_path) / AttachmentStore.RELDIR))
    assert Path(view.path).read_bytes() == b"\x89PNG"


def test_an_upload_excludes_the_directory_from_git(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Not defensiveness: a workspace that predates this feature has no such
    exclude, and its first attachment is the moment an untracked file starts
    blocking its own ``pause`` and ``kill``."""
    state = _workspace(manager)

    manager.add_attachment(state.id, "notes.md", b"hi")

    excludes = (tmp_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert AttachmentStore.RELDIR + "/" in excludes.splitlines()


def test_an_oversize_upload_is_a_grove_error(manager: WorkspaceManager) -> None:
    state = _workspace(manager)

    with pytest.raises(GroveError, match="over the"):
        manager.add_attachment(state.id, "big.bin", b"x" * (AttachmentStore.MAX_BYTES + 1))


def test_a_container_workspace_is_told_its_own_namespace_s_path(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The ``GROVE_PHASE_FILE`` rule applied to a second artifact under the same
    worktree: a container gets the path re-rooted off the folder it reported,
    never a host path that resolves to nothing inside it."""
    state = _workspace(manager)
    stored = manager.store.get(state.id)
    stored.runtime = Runtime.CONTAINER
    stored.container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref="ghcr.io/example/dev:1",
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels=ContainerRuntimeState.labels_for(state.id),
    )
    manager.store.save(stored)

    view = manager.add_attachment(state.id, "diagram.png", b"\x89PNG")

    assert view.path.startswith(FAKE_REMOTE_FOLDER)
    assert AttachmentStore.RELDIR in view.path


def test_a_message_carrying_attachments_appends_one_grove_fenced_block(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _workspace(manager)
    first = manager.add_attachment(state.id, "a.png", b"1")
    second = manager.add_attachment(state.id, "b.md", b"2")

    manager.send_message(state.id, "look at these", attachments=[first.id, second.id])

    ((_target, text),) = fake_tmux.sent_texts
    assert text.startswith("look at these\n\n")
    assert '<grove-instruction kind="attachments">' in text
    assert f"- a.png — {first.path}" in text
    assert f"- b.md — {second.path}" in text


def test_a_message_with_no_attachments_is_byte_identical_to_before(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _workspace(manager)

    manager.send_message(state.id, "plain steer")

    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "plain steer")]


def test_an_attachment_id_that_no_longer_resolves_is_skipped_not_refused(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The file may have gone with a ``pause`` that removed the worktree.
    Dropping a message a human typed because one of its attachments expired is
    the worse failure — the block states what it found."""
    state = _workspace(manager)
    kept = manager.add_attachment(state.id, "kept.txt", b"here")

    manager.send_message(state.id, "still send this", attachments=["0123456789ab", kept.id])

    ((_target, text),) = fake_tmux.sent_texts
    assert "still send this" in text
    assert "1 file" in text
    assert "kept.txt" in text
