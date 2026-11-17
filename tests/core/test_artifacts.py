"""Workspace artifacts: target validation, the read boundary, and manager attach/detach.

Real files and a real git worktree, no network. Every test pins ``roots`` to
an explicit set: pytest's own tmp dir sits under ``/tmp`` on Linux and outside
it on macOS, so the default root would make "outside" mean different things on
the two.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.artifacts import MAX_ARTIFACT_BYTES, ArtifactFiles
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.public import PublicWorkspaceStateView
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ArtifactInvalid, ArtifactUnavailable
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

# ─── ArtifactFiles: classify, contain, read ─────────────────────────────────


@pytest.fixture
def root(tmp_path: Path) -> Path:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    return allowed


def test_a_link_is_remembered_verbatim(root: Path) -> None:
    ref = ArtifactFiles((root,)).resolve("https://example.com/doc?x=1", base=root, title="Doc")
    assert (ref.kind, ref.target, ref.title) == ("link", "https://example.com/doc?x=1", "Doc")


@pytest.mark.parametrize("target", ["javascript://alert(1)", "file:///etc/hosts", "https://"])
def test_a_link_that_is_not_http_is_refused(root: Path, target: str) -> None:
    with pytest.raises(ArtifactInvalid):
        ArtifactFiles((root,)).resolve(target, base=root)


def test_a_relative_path_resolves_against_the_base_and_reads_live_bytes(root: Path) -> None:
    plan = root / "docs" / "plan.md"
    plan.parent.mkdir()
    plan.write_text("v1", encoding="utf-8")
    files = ArtifactFiles((root,))
    ref = files.resolve("docs/plan.md", base=root)
    assert (ref.kind, ref.target) == ("file", str(plan))
    plan.write_text("v2", encoding="utf-8")
    content = files.read(ref)
    assert (content.name, content.data) == ("plan.md", b"v2")


def test_the_same_target_always_gets_the_same_id(root: Path) -> None:
    (root / "a.md").write_text("x", encoding="utf-8")
    files = ArtifactFiles((root,))
    assert files.resolve("a.md", base=root).id == files.resolve(str(root / "a.md"), base=root).id


def test_a_file_outside_every_root_is_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(ArtifactInvalid, match="outside the allowed"):
        ArtifactFiles((root,)).resolve(str(outside), base=root)
    # `..` is lexical traversal out of the base; the real path decides.
    with pytest.raises(ArtifactInvalid):
        ArtifactFiles((root,)).resolve("../secret.txt", base=root)


def test_a_symlink_that_leaves_the_root_is_refused_at_attach(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(ArtifactInvalid):
        ArtifactFiles((root,)).resolve("link.txt", base=root)


def test_a_file_swapped_for_an_escaping_symlink_is_refused_at_read(
    root: Path, tmp_path: Path
) -> None:
    """The incident shape: valid when attached, pointed elsewhere afterwards."""
    outside = tmp_path / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    inside = root / "plan.md"
    inside.write_text("ok", encoding="utf-8")
    files = ArtifactFiles((root,))
    ref = files.resolve("plan.md", base=root)
    inside.unlink()
    inside.symlink_to(outside)
    with pytest.raises(ArtifactUnavailable):
        files.read(ref)


def test_a_missing_file_a_directory_and_a_link_have_no_content(root: Path) -> None:
    files = ArtifactFiles((root,))
    with pytest.raises(ArtifactInvalid):
        files.resolve("nope.md", base=root)
    with pytest.raises(ArtifactInvalid):
        files.resolve(".", base=root)
    with pytest.raises(ArtifactInvalid):
        files.read(files.resolve("https://example.com", base=root))


def test_an_oversized_file_is_refused_rather_than_truncated(root: Path) -> None:
    big = root / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(MAX_ARTIFACT_BYTES + 1)
    files = ArtifactFiles((root,))
    with pytest.raises(ArtifactInvalid, match="32 MiB"):
        files.read(files.resolve("big.bin", base=root))


# ─── manager: attach / detach / read against a real worktree ────────────────


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "grove/"},
            "tmux": {"session_prefix": "test-"},
            "artifacts": {"roots": [str(tmp_path / "extra")]},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


def _workspace(manager: WorkspaceManager) -> tuple[str, Path]:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="artifacts"))
    return state.id, Path(state.worktree_path)


def test_attach_is_idempotent_and_a_new_title_renames_in_place(manager: WorkspaceManager) -> None:
    ws_id, worktree = _workspace(manager)
    (worktree / "plan.md").write_text("plan", encoding="utf-8")

    first = manager.attach_artifact(ws_id, "plan.md")
    again = manager.attach_artifact(ws_id, str(worktree / "plan.md"))
    assert [r.target for r in again.artifact_refs] == [str(worktree / "plan.md")]
    assert again.updated_at == first.updated_at

    renamed = manager.attach_artifact(ws_id, "plan.md", "The plan")
    assert [(r.id, r.title) for r in renamed.artifact_refs] == [
        (first.artifact_refs[0].id, "The plan")
    ]
    # A later attach without a title keeps the stored one.
    assert manager.attach_artifact(ws_id, "plan.md").artifact_refs[0].title == "The plan"


def test_the_three_roots_are_the_worktree_the_project_and_the_configured_ones(
    manager: WorkspaceManager, tmp_repo: Path, tmp_path: Path
) -> None:
    ws_id, worktree = _workspace(manager)
    (worktree / "a.drawio").write_text("<mxfile/>", encoding="utf-8")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "report.html").write_text("<p>r</p>", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("no", encoding="utf-8")

    manager.attach_artifact(ws_id, "a.drawio")
    manager.attach_artifact(ws_id, str(tmp_repo / "README.md"))
    manager.attach_artifact(ws_id, str(extra / "report.html"))
    state = manager.attach_artifact(ws_id, "https://example.com/doc")
    assert [r.kind for r in state.artifact_refs] == ["file", "file", "file", "link"]
    with pytest.raises(ArtifactInvalid):
        manager.attach_artifact(ws_id, str(elsewhere))


def test_refs_survive_a_store_round_trip_and_read_serves_only_attached_ids(
    manager: WorkspaceManager, tmp_path: Path
) -> None:
    ws_id, worktree = _workspace(manager)
    (worktree / "plan.md").write_text("plan", encoding="utf-8")
    ref = manager.attach_artifact(ws_id, "plan.md", "Plan").artifact_refs[0]

    reloaded = JsonWorkspaceStore(path=tmp_path / "state.json").get(ws_id)
    assert reloaded.artifact_refs == [ref]
    assert manager.read_artifact(ws_id, ref.id).data == b"plan"
    with pytest.raises(ArtifactUnavailable):
        manager.read_artifact(ws_id, "0" * 12)


def test_detach_accepts_the_id_the_path_or_the_url_and_survives_a_deleted_file(
    manager: WorkspaceManager,
) -> None:
    ws_id, worktree = _workspace(manager)
    for name in ("a.md", "b.md"):
        (worktree / name).write_text(name, encoding="utf-8")
    manager.attach_artifact(ws_id, "a.md")
    manager.attach_artifact(ws_id, "b.md")
    state = manager.attach_artifact(ws_id, "https://example.com/doc")
    a_id = state.artifact_refs[0].id

    (worktree / "b.md").unlink()
    assert len(manager.detach_artifact(ws_id, a_id).artifact_refs) == 2
    assert len(manager.detach_artifact(ws_id, "b.md").artifact_refs) == 1
    assert manager.detach_artifact(ws_id, "https://example.com/doc").artifact_refs == []
    # Unknown ref: a no-op, not an error.
    assert manager.detach_artifact(ws_id, "never-attached").artifact_refs == []


# ─── boundaries the feature must not widen ──────────────────────────────────


def test_a_committed_project_config_cannot_widen_the_roots(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A cloned repository must not choose which host directories are readable."""
    del tmp_state_dir
    target = paths_mod.project_config_path(tmp_repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"artifacts": {"roots": ["/"]}}', encoding="utf-8")
    assert load_config(tmp_repo, env={}).artifacts.roots == ("/tmp",)


def test_artifacts_never_reach_the_public_share_view() -> None:
    assert "artifact_refs" not in PublicWorkspaceStateView.model_fields
