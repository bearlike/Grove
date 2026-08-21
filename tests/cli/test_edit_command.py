"""``grove edit`` — the metadata write verb, and the identity `show` reads back.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam, the
same shape ``tests/cli/test_show_command.py`` uses. ``edit`` is a thin shell
over ``WorkspaceManager.update``, so these tests pin the SHELL's contract — cwd
inference, the refusal with no flags, the clear convention, and that a rename
moves no derived path — rather than re-testing the engine's normalisation,
which ``tests/core/test_workspace_update.py`` already owns.

The terminal-hyperlink and ticket-line renderers are pure functions and are
exercised directly, because their whole contract is the string they produce.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.contracts.tickets import TicketRef
from grove.core.phase import TicketClaim
from grove.tui.cli import app
from grove.tui.cli_workspace import _hyperlink, _ticket_line
from tests.conftest import FakeTmux


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str = "edit me") -> str:
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


def _row(runner: CliRunner, ws_id: str) -> dict[str, object]:
    """The workspace record as `WorkspaceStateView`.

    Read through ``show --json`` rather than ``ls``: ``ls`` serialises a
    narrower listing shape that carries no ``description``, which is the field
    half these tests are about.
    """
    shown = runner.invoke(app, ["show", ws_id, "--json"])
    assert shown.exit_code == 0, shown.output
    return dict(json.loads(shown.output))


# ─── grove edit ──────────────────────────────────────────────────────────────


def test_edit_renames_and_sets_description(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["edit", ws_id[:8], "--title", "renamed", "-d", "why it exists"])
    assert result.exit_code == 0, result.output

    row = _row(runner, ws_id)
    assert row["title"] == "renamed"
    assert row["description"] == "why it exists"


def test_edit_infers_the_workspace_from_the_cwd(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape an agent inside its own worktree uses: no id to look up first."""
    del project
    ws_id = _create(runner)
    monkeypatch.chdir(Path(str(_row(runner, ws_id)["worktree_path"])))

    result = runner.invoke(app, ["edit", "--title", "named from inside"])
    assert result.exit_code == 0, result.output
    assert _row(runner, ws_id)["title"] == "named from inside"


def test_edit_with_no_fields_refuses(runner: CliRunner, project: Path) -> None:
    """An update that changes nothing must not report success — it would read as
    a rename that silently did not happen."""
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["edit", ws_id[:8]])
    assert result.exit_code == 1
    assert "nothing to change" in result.output
    assert "Traceback" not in result.output


def test_edit_empty_description_clears_it(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)
    assert runner.invoke(app, ["edit", ws_id[:8], "-d", "temporary"]).exit_code == 0

    result = runner.invoke(app, ["edit", ws_id[:8], "--description", ""])
    assert result.exit_code == 0, result.output
    assert _row(runner, ws_id)["description"] is None


def test_rename_moves_neither_the_worktree_nor_the_session(
    runner: CliRunner, project: Path
) -> None:
    """Title seeds the worktree path and tmux session at CREATE only. A rename
    that moved either would break every already-attached client."""
    del project
    ws_id = _create(runner)
    before = _row(runner, ws_id)

    assert runner.invoke(app, ["edit", ws_id[:8], "--title", "totally different"]).exit_code == 0

    after = _row(runner, ws_id)
    assert after["worktree_path"] == before["worktree_path"]
    assert after["tmux_session"] == before["tmux_session"]
    assert after["branch"] == before["branch"]


def test_edit_json_emits_the_workspace_state_view(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["edit", ws_id[:8], "--title", "as json", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == ws_id
    assert payload["title"] == "as json"


# ─── grove show --json + the identity block ──────────────────────────────────


def test_show_json_emits_the_workspace_state_view(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)
    assert runner.invoke(app, ["edit", ws_id[:8], "-d", "the description"]).exit_code == 0

    result = runner.invoke(app, ["show", ws_id[:8], "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == ws_id
    assert payload["description"] == "the description"


def test_show_renders_the_description(runner: CliRunner, project: Path) -> None:
    """`show` is where a person reads the field `edit` writes; before this it
    printed everything about a workspace except the one line explaining it."""
    del project
    ws_id = _create(runner)
    assert runner.invoke(app, ["edit", ws_id[:8], "-d", "spike, do not merge"]).exit_code == 0

    result = runner.invoke(app, ["show", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "spike, do not merge" in result.output


# ─── the terminal-hyperlink and ticket-line renderers (pure) ─────────────────


def test_hyperlink_wraps_the_label_in_osc8_when_attached_to_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    linked = _hyperlink("gitea#42", "https://example.test/issues/42")
    assert linked == "\033]8;;https://example.test/issues/42\033\\gitea#42\033]8;;\033\\"
    # The URL costs no rendered width — which is the whole reason the link can
    # ride a line that already crops.
    assert "gitea#42" in linked


def test_hyperlink_is_plain_text_when_stdout_is_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An escape sequence in a pipe is corruption, not presentation."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert _hyperlink("gitea#42", "https://example.test/issues/42") == "gitea#42"


def test_hyperlink_without_a_url_is_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _hyperlink("gitea#42", None) == "gitea#42"


def test_ticket_line_renders_title_status_and_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    ref = TicketRef(provider="gitea", id="42", title="Fix the thing", status="open")
    claim = TicketClaim(ticket="gitea:42", phase="verifying")

    line = _ticket_line(ref, claim)
    assert "gitea#42" in line
    assert "Fix the thing" in line
    assert "[open]" in line
    assert "verifying" in line


def test_ticket_line_omits_every_absent_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare ref is the ordinary case — an unresolved title, an unreported
    phase and a non-ambiguous ref must each render as nothing, not as a
    placeholder that buries the refs which do carry information."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    line = _ticket_line(TicketRef(provider="gitea", id="42"))
    assert line.strip() == "gitea#42  (issue)"


def test_ticket_line_marks_a_blocked_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    claim = TicketClaim(ticket="gitea:42", phase="implementing", blocked=True)
    line = _ticket_line(TicketRef(provider="gitea", id="42"), claim)
    assert "implementing" in line
    assert "blocked" in line


def test_ticket_line_names_a_pull_request_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    line = _ticket_line(TicketRef(provider="gitea", id="7", kind="pull_request"))
    assert "(PR)" in line
