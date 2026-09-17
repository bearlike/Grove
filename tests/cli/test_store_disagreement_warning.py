"""Grove SAYS when it resolved a store that is not the one that launched it.

Measured 2026-09-17: a pytest run exported ``XDG_CONFIG_HOME``/``XDG_STATE_HOME``
into a long-lived tmux server, so every `grove` descending from it read a
fixture's workspace store. ``grove ls`` showed 1 workspace where the repo had 3,
``grove fleet`` showed 3 fictional ones, ``grove mailbox peers`` showed 7, and
the overlap between the last two was ZERO — every surface internally consistent,
none of them saying why, and the obvious reading ("my workspace was never
registered") wrong.

**The cascade honouring ``XDG_*`` is deliberate and is NOT what these tests
pin** — the screenshot sandbox depends on it. What is pinned is that the
disagreement becomes visible: `grove debug` distinguishes an env-overridden path
from a default one, and a CLI whose own workspace is absent from the store it
resolved says so once on stderr and still succeeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import paths
from grove.core.phase import PhaseFile
from grove.tui.cli import app
from grove.tui.cli_workspace import warn_if_store_disowns_caller


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ─── seam 1: `grove debug` says WHY a path resolved where it did ─────────────


def test_debug_reports_no_overrides_by_default(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)

    payload = json.loads(runner.invoke(app, ["debug"]).stdout)

    assert payload["path_overrides"] == {}


def test_debug_names_the_variable_that_redirected_the_store(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of the seam: `debug` already printed the paths, and a
    path alone cannot say whether Grove chose it or an environment did."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    payload = json.loads(runner.invoke(app, ["debug"]).stdout)

    assert payload["path_overrides"] == {"XDG_STATE_HOME": str(tmp_path / "xdg-state")}


def test_dir_overrides_ignores_an_empty_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported-but-empty var does not redirect anything, so reporting it
    would send a reader after a cause that is not there."""
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert paths.dir_overrides() == {}


# ─── seam 2: the store that does not contain its own caller ─────────────────


def test_warns_when_the_store_lacks_the_callers_own_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE DISAGREEMENT CASE the issue asks for: the phase file names an id the
    resolved store does not have. The store here is a real, VALID, non-empty
    store — it is simply somebody else's, which is exactly the shape that read
    as "my workspace was never registered"."""
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/some/worktree/.grove/phase/" + "f" * 32 + ".json")

    message = warn_if_store_disowns_caller()

    assert message is not None
    assert "f" * 12 in message
    assert "grove debug" in message
    assert "f" * 12 in capsys.readouterr().err


def test_the_warning_names_the_env_override_as_the_remedy(
    monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "leaked"))
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/some/worktree/.grove/phase/" + "a" * 32 + ".json")

    message = warn_if_store_disowns_caller()

    assert message is not None
    assert "XDG_STATE_HOME" in message


def test_silent_when_the_store_does_contain_the_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_repo: Path, fake_tmux: object
) -> None:
    """The no-false-positive case: a correctly-launched session must never see
    this line, or it becomes noise everybody learns to ignore."""
    from grove.core import CreateWorkspaceRequest, build  # noqa: PLC0415

    monkeypatch.chdir(tmp_repo)
    state = build(tmp_repo).create(CreateWorkspaceRequest(agent_name="claude", title="mine"))
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(PhaseFile.path_for(tmp_repo, state.id)))

    assert warn_if_store_disowns_caller() is None


def test_silent_for_a_human_with_no_phase_file(
    monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path
) -> None:
    monkeypatch.delenv(PhaseFile.PATH_ENV, raising=False)

    assert warn_if_store_disowns_caller() is None


def test_ls_still_succeeds_while_warning(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A diagnosis rides BESIDE the result, never instead of it — and it goes to
    stderr so `grove ls | jq` is unaffected."""
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setenv(PhaseFile.PATH_ENV, "/some/worktree/.grove/phase/" + "b" * 32 + ".json")

    result = runner.invoke(app, ["ls"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
