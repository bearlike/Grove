"""The CLI resolves the CALLER'S OWN workspace, not whichever one shares its cwd.

``grove phase`` / ``grove edit`` and every other cwd-inferring verb used to ask
"which workspace is this directory", which in a shared worktree is a different
question from "which workspace is asking". Measured 2026-09-17: a session whose
own id was ``1d58b613…`` ran ``grove phase scoping`` from the main checkout and
published the claim onto ``845d3e8f…`` — a different task, with issue #785
attached, already merged — walking that ticket's phase backwards from ``done``
to ``scoping`` and reporting success.

``GROVE_PHASE_FILE`` already names the caller's own workspace unambiguously, so
preferring it makes the CLI and the file agree by construction.

Each test here is written so it can only pass if the behaviour under test
exists — see the module-level note on each: the ambiguity case uses TWO
workspaces sharing ONE worktree (a single-workspace fixture would reach the
right outcome through the "no tie" branch and prove nothing), and the env-var
case is built so that cwd inference would resolve a DIFFERENT, existing
workspace rather than raising.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import GroveError, WorkspaceState
from grove.core.phase import PhaseFile
from grove.core.workspace import WorkspaceStatus
from grove.tui.cli_workspace import own_workspace_ref, resolve_or_infer_workspace


def _state(ws_id: str, worktree: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"title-{ws_id}",
        repo_root=str(worktree.parent),
        branch=f"grove/{ws_id}",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


class _FakeManager:
    """Duck-typed manager exposing only ``.list()`` — the resolver's cwd seam."""

    def __init__(self, states: list[WorkspaceState]) -> None:
        self._states = states

    def list(self) -> list[WorkspaceState]:
        return self._states


# ─── own_workspace_ref: the id lives in the FILENAME ─────────────────────────


def test_own_workspace_ref_reads_the_id_from_the_phase_file_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/w/tree/.grove/phase/abc123.json")
    assert own_workspace_ref() == "abc123"


def test_own_workspace_ref_ignores_the_agent_slot_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``key_for`` joins workspace and slot with '.', so a co-tenant agent in one
    container must still resolve to the WORKSPACE, not to ``abc123.reviewer``."""
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/w/tree/.grove/phase/abc123.reviewer.json")
    assert own_workspace_ref() == "abc123"


def test_own_workspace_ref_reads_a_container_path_this_host_cannot_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A containerized agent's variable holds a path in the CONTAINER's namespace.

    Every leading component is meaningless on this host; the basename is the one
    part Grove composed. Reading the filename rather than locating the file is
    what makes this work — and a `Path.exists()` check would silently disable
    the whole fix for every containerized workspace.
    """
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/workspaces/repo/.grove/phase/deadbeef.json")
    assert own_workspace_ref() == "deadbeef"


def test_own_workspace_ref_is_none_without_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human at a shell has no own workspace — cwd inference is right for them."""
    monkeypatch.delenv(PhaseFile.PATH_ENV, raising=False)
    assert own_workspace_ref() is None


def test_own_workspace_ref_refuses_the_legacy_single_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``.grove/phase.json`` names a workspace only by sitting inside one — the
    cwd-shaped inference the per-agent layout exists to replace. Parsing it
    would yield the literal ``"phase"`` as an id."""
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/w/tree/" + PhaseFile.LEGACY_RELPATH)
    assert own_workspace_ref() is None


# ─── the env var beats cwd inference ─────────────────────────────────────────


def test_phase_file_wins_over_a_cwd_that_resolves_a_DIFFERENT_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION TEST FOR THE REPORTED INCIDENT.

    Two workspaces, one shared root. The caller IS ``mine``; the cwd contains
    only ``stranger``, so cwd inference has an unambiguous — and wrong — answer
    and would resolve it silently. The fixture is built this way on purpose: if
    the cwd resolved nothing, or resolved a tie, the test would pass through the
    "not found" or "refuse" branch and say nothing about precedence.
    """
    root = tmp_path / "repo"
    root.mkdir()
    stranger = _state("5711a11e" + "0" * 24, root)
    mine = _state("bafa79e0" + "0" * 24, root)
    monkeypatch.chdir(root)
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(PhaseFile.path_for(root, mine.id)))

    # The store answers the env-var branch; only `stranger` is visible by cwd.
    monkeypatch.setattr(
        "grove.tui.cli_workspace.resolve_workspace",
        lambda ref: (_FakeManager([mine]), mine) if mine.id.startswith(ref) else None,
    )
    resolved = resolve_or_infer_workspace(None, manager=_FakeManager([stranger]))[1]

    assert resolved.id == mine.id, "the CLI targeted a workspace the caller does not own"


def test_an_unresolvable_own_id_REFUSES_rather_than_falling_back_to_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE WRONG-STORE INCIDENT, END TO END — and the case a mutation caught.

    This is the measured 2026-09-17 shape: the caller's own id is absent from
    the resolved store (a leaked ``XDG_STATE_HOME`` pointed Grove at a pytest
    fixture's store) while the cwd still contains a perfectly real stranger
    whose ticket #785 was already merged. "Grove launched me and cannot find
    me" must REFUSE — a fallback to cwd here silently resolves the stranger and
    publishes onto their ticket, which is the entire bug.

    Written as a mutation test first: replacing the ``raise`` with a fallback
    left every other test in this file green, so the refusal was untested while
    reading as covered.
    """
    root = tmp_path / "repo"
    root.mkdir()
    stranger = _state("5711a11e" + "0" * 24, root)
    monkeypatch.chdir(root)
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(PhaseFile.path_for(root, "a" * 32)))

    def _absent(ref: str) -> tuple[_FakeManager, WorkspaceState]:
        raise GroveError(f"no workspace matches {ref!r}")

    monkeypatch.setattr("grove.tui.cli_workspace.resolve_workspace", _absent)

    with pytest.raises(GroveError, match="no workspace matches"):
        resolve_or_infer_workspace(None, manager=_FakeManager([stranger]))


def test_an_explicit_ref_still_beats_the_phase_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence is explicit ref > env var > cwd: a person naming a workspace
    means that workspace, even from inside another one."""
    root = tmp_path / "repo"
    root.mkdir()
    named = _state("11111111" + "0" * 24, root)
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(PhaseFile.path_for(root, "2222" + "0" * 28)))
    monkeypatch.setattr(
        "grove.tui.cli_workspace.resolve_workspace",
        lambda ref: (_FakeManager([named]), named),
    )

    assert resolve_or_infer_workspace(named.id)[1].id == named.id


def test_cwd_inference_still_answers_when_no_phase_file_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The single-match case is unchanged — this is the human-at-a-shell path,
    and the issue explicitly did not propose removing it."""
    root = tmp_path / "repo"
    worktree = root / ".worktrees" / "feature"
    worktree.mkdir(parents=True)
    only = _state("ce11ed" + "0" * 26, worktree)
    monkeypatch.delenv(PhaseFile.PATH_ENV, raising=False)
    monkeypatch.chdir(worktree)

    assert resolve_or_infer_workspace(None, manager=_FakeManager([only]))[1].id == only.id


def test_ambiguous_cwd_refuses_and_names_the_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TWO workspaces sharing ONE worktree — the acceptance criterion's own
    shape, and the reason it is specified that way: with a single workspace the
    resolver reaches the same 'did not silently pick the wrong one' outcome
    through a branch that has nothing to do with the refusal.
    """
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.delenv(PhaseFile.PATH_ENV, raising=False)
    monkeypatch.chdir(root)
    first = _state("aaaa1111" + "0" * 24, root)
    second = _state("bbbb2222" + "0" * 24, root)

    with pytest.raises(GroveError) as excinfo:
        resolve_or_infer_workspace(None, manager=_FakeManager([first, second]))

    message = str(excinfo.value)
    assert "2 workspaces share this directory" in message
    assert first.id in message
    assert second.id in message
