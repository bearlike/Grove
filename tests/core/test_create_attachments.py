"""Attachments that ride the CREATE, for the composer with no workspace yet.

The landing composer's whole action is "here is a prompt, make me a workspace",
so there is no id to upload against until the thing being described exists. The
files therefore travel on `CreateWorkspaceRequest` and the engine stores them the
moment the worktree does, which is what keeps `initial_prompt`'s race-free
launch-argv delivery intact.

What these pin, beyond "it works": that a create WITHOUT attachments is
byte-identical to what it was before the field existed, and that the text an
agent ends up in front of is the SAME Grove-fenced block a follow-up message
produces. Two ways of attaching a file that read differently to the agent would
be the drift worth catching.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from grove.core.attachments import AttachmentStore
from grove.core.config import GroveConfig
from grove.core.contracts.attachments import AttachmentUploadRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # installed via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
            # The brief would prepend its own paragraphs to the prompt; these
            # tests are about the attachment block's placement, and the brief
            # has its own suite.
            "brief": {"enabled": False},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


def _upload(name: str, data: bytes) -> AttachmentUploadRequest:
    return AttachmentUploadRequest(name=name, content_base64=base64.b64encode(data).decode("ascii"))


def _prompt_of(fake_tmux: FakeTmux, state: WorkspaceState) -> str:
    """The trailing positional the launch argv carries — the agent's first task."""
    decoration = next(
        deco for session, deco in fake_tmux.launch_decorations if session == state.tmux_session
    )
    return decoration[-1]


def test_a_create_with_no_attachments_is_unchanged(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The field defaults, so every client that predates it keeps working and
    its launch argv is what it always was."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="plain", initial_prompt="fix the parser")
    )

    assert _prompt_of(fake_tmux, state) == "fix the parser"


def test_attached_files_land_in_the_worktree_and_are_named_in_the_launch_prompt(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="with files",
            initial_prompt="use these",
            attachments=[_upload("spec.md", b"# spec"), _upload("shot.png", b"\x89PNG")],
        )
    )

    root = Path(state.worktree_path) / AttachmentStore.RELDIR
    written = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert written == ["shot.png", "spec.md"]

    prompt = _prompt_of(fake_tmux, state)
    assert prompt.startswith("use these\n\n")
    assert '<grove-instruction kind="attachments">' in prompt
    assert "- spec.md — " in prompt
    assert "- shot.png — " in prompt
    # The paths named are the ones actually on disk, not a shape rebuilt by hand.
    for path in (p for p in root.rglob("*") if p.is_file()):
        assert str(path) in prompt


def test_the_block_is_identical_to_the_one_a_follow_up_message_produces(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Two ways of attaching a file that read differently to the agent would be
    a real drift; one renderer serves both, and this is what says so."""
    created = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="both roads",
            initial_prompt="look",
            attachments=[_upload("notes.md", b"hi")],
        )
    )
    launch_block = _prompt_of(fake_tmux, created).split("\n\n", 1)[1]

    later = manager.add_attachment(created.id, "notes.md", b"hi")
    fake_tmux.sent_texts.clear()
    manager.send_message(created.id, "look", attachments=[later.id])
    (_target, message) = fake_tmux.sent_texts[-1]
    message_block = message.split("\n\n", 1)[1]

    # Same fence, same row shape, same wording — only the path's id segment,
    # which is minted per attachment, legitimately differs.
    assert launch_block.splitlines()[0] == message_block.splitlines()[0]
    assert launch_block.count("- notes.md — ") == message_block.count("- notes.md — ")
    assert "read whichever you need" in launch_block


def test_files_with_no_prompt_become_the_prompt(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The one place composing a prompt is honest rather than invented: the
    person attached files and asked for a workspace, so the files ARE the
    message. Contrast the brief, which refuses to become a task on its own."""
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="files only", attachments=[_upload("a.txt", b"x")]
        )
    )

    prompt = _prompt_of(fake_tmux, state)
    assert prompt.startswith('<grove-instruction kind="attachments">')
    assert "- a.txt — " in prompt


def test_a_malformed_payload_fails_the_create_rather_than_half_landing(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Loud, and rolled back: a workspace whose prompt names files that never
    arrived is worse than no workspace."""
    bad = AttachmentUploadRequest(name="x.bin", content_base64="not base64 at all!!")

    with pytest.raises(GroveError, match="not valid base64"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude", title="doomed", initial_prompt="go", attachments=[bad]
            )
        )

    assert list(manager.list()) == []
    assert not (tmp_repo / ".worktrees").exists() or not any((tmp_repo / ".worktrees").iterdir())


def test_an_oversize_attachment_fails_the_create(manager: WorkspaceManager) -> None:
    huge = _upload("big.bin", b"x" * (AttachmentStore.MAX_BYTES + 1))

    with pytest.raises(GroveError, match="over the"):
        manager.create(
            CreateWorkspaceRequest(agent_name="claude", title="too big", attachments=[huge])
        )

    assert list(manager.list()) == []


def test_the_request_caps_how_many_files_one_create_may_carry() -> None:
    """One request body, so the count needs a bound as much as each file does."""
    with pytest.raises(ValueError, match="at most 20"):
        CreateWorkspaceRequest(
            agent_name="claude",
            title="too many",
            attachments=[_upload(f"f{i}.txt", b"x") for i in range(21)],
        )


def test_the_optional_attachments_field_emits_no_schema_default() -> None:
    """An optional field that carries a ``default`` GENERATES AS REQUIRED in TS.

    `openapi-typescript`'s `defaultNonNullable` is on by default in v7 and makes
    any property carrying a `default` non-optional — regardless of the schema's
    own `required` array. So `Field(default=[])` produced
    `attachments: AttachmentUploadRequest[]` with no `?`, and an unrelated
    caller that builds a `CreateWorkspaceRequest` literal stopped typechecking
    on a field it has nothing to do with.

    **Every other optional field on this model escapes that only by accident**:
    Pydantic emits no `default` key for them at all — `branch_plan` is `{}` in
    the schema despite defaulting to `AutoBranch()` — so nobody had met the trap
    before. `default_factory` is what restores that, and it drops a shared
    mutable default on the way past.

    Pinned as a SCHEMA assertion rather than a TS one because the schema is the
    boundary: this suite cannot run `openapi-typescript`, but it can hold the
    input that tool reads. `branch_plan` rides along as the control — if
    Pydantic ever starts emitting defaults for both, this test says so rather
    than passing for the wrong reason.
    """
    schema = CreateWorkspaceRequest.model_json_schema()
    attachments = schema["properties"]["attachments"]

    assert "default" not in attachments, (
        "a `default` here generates a REQUIRED TypeScript property; "
        "use `default_factory` for an optional collection"
    )
    assert "attachments" not in schema.get("required", [])
    assert "default" not in schema["properties"]["branch_plan"]
