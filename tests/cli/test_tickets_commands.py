"""``grove tickets attach/list/detach`` — the CLI face of workspace-links.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux, no network. Resolution (URL / ``#42`` /
``owner/repo#42`` -> provider + id + kind) is entirely engine-side
(``WorkspaceManager.attach_link`` / ``ticket_providers.resolve_link``), so
these tests exercise the real thing, not a stub: a project config enabling
one or two PURE (no-token) ticket providers is written into the repo the same
way ``test_init_script_scope.py`` writes one for ``init_script``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import paths as paths_mod
from grove.core.contracts.tickets import TicketRef
from grove.tui.cli import app
from grove.tui.cli_workspace import _emit_ticket_refs
from tests.conftest import FakeTmux


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_tickets_config(repo: Path, tickets: dict[str, object]) -> None:
    target = paths_mod.project_config_path(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"tickets": tickets}), encoding="utf-8")


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    """cwd inside a real repo with ONE enabled (tokenless) gitea provider."""
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    _write_tickets_config(
        tmp_repo,
        {
            "gitea": {
                "enabled": True,
                "owner": "acme",
                "repo": "api",
                "base_url": "https://gitea.example.com",
            }
        },
    )
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str = "link me") -> str:
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


def _worktree_of(runner: CliRunner, ws_id: str) -> Path:
    listed = runner.invoke(app, ["ls"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    match = next(r for r in rows if r["id"] == ws_id)
    return Path(match["worktree_path"])


# ─── attach ─────────────────────────────────────────────────────────────────


def test_attach_bare_number_cwd_inferred(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common shape: an agent in its own worktree pastes a bare number
    with no --workspace — one enabled provider resolves it with no guessing."""
    del project
    ws_id = _create(runner)
    worktree = _worktree_of(runner, ws_id)
    monkeypatch.chdir(worktree)

    result = runner.invoke(app, ["tickets", "attach", "#42"])
    assert result.exit_code == 0, result.output
    assert f"attached to {ws_id}" in result.output
    assert "gitea#42" in result.output
    assert "(issue)" in result.output


def test_attach_pull_request_url_explicit_workspace(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(
        app,
        [
            "tickets",
            "attach",
            "https://gitea.example.com/acme/api/pulls/7",
            "--workspace",
            ws_id[:8],
        ],
    )
    assert result.exit_code == 0, result.output
    assert "gitea#7" in result.output
    assert "(PR)" in result.output


def test_attach_is_idempotent_and_corrects_kind_in_place(runner: CliRunner, project: Path) -> None:
    """Re-attaching the same (provider, id) with a different kind corrects it
    rather than growing a duplicate row (mirrors the engine's own contract)."""
    del project
    ws_id = _create(runner)
    runner.invoke(app, ["tickets", "attach", "#9", "--workspace", ws_id[:8]])

    result = runner.invoke(
        app,
        ["tickets", "attach", "https://gitea.example.com/acme/api/pull/9", "-w", ws_id[:8]],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("gitea#9") == 1
    assert "(PR)" in result.output


def test_attach_ambiguous_across_two_enabled_providers_is_a_clean_error(
    runner: CliRunner, project: Path
) -> None:
    _write_tickets_config(
        project,
        {
            "gitea": {"enabled": True, "owner": "acme", "repo": "api"},
            "github": {"enabled": True, "owner": "acme", "repo": "api"},
        },
    )
    ws_id = _create(runner)

    result = runner.invoke(app, ["tickets", "attach", "42", "--workspace", ws_id[:8]])
    assert result.exit_code == 1
    assert "could be" in result.output
    assert "Traceback" not in result.output


def test_attach_unknown_workspace_is_a_clean_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["tickets", "attach", "#1", "--workspace", "deadbeef"])
    assert result.exit_code == 1
    assert "no workspace matches" in result.output


# ─── list ───────────────────────────────────────────────────────────────────


def test_list_reports_no_tickets_by_default(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["tickets", "list", "--workspace", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "(no tickets attached)" in result.output


def test_list_shows_an_attached_ticket(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)
    runner.invoke(app, ["tickets", "attach", "#3", "--workspace", ws_id[:8]])

    result = runner.invoke(app, ["tickets", "list", "--workspace", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "gitea#3" in result.output
    assert "(issue)" in result.output


# ─── detach ─────────────────────────────────────────────────────────────────


def test_detach_removes_a_ticket(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)
    runner.invoke(app, ["tickets", "attach", "#5", "--workspace", ws_id[:8]])

    result = runner.invoke(app, ["tickets", "detach", "#5", "--workspace", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "detached gitea#5" in result.output

    listed = runner.invoke(app, ["tickets", "list", "--workspace", ws_id[:8]])
    assert "(no tickets attached)" in listed.output


def test_detach_never_attached_is_a_noop(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["tickets", "detach", "#99", "--workspace", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "detached gitea#99" in result.output


# ─── _emit_ticket_refs renderer (pure, no CliRunner) ───────────────────────


def test_emit_ticket_refs_empty(capsys: pytest.CaptureFixture[str]) -> None:
    _emit_ticket_refs([])
    assert "(no tickets attached)" in capsys.readouterr().out


def test_emit_ticket_refs_marks_ambiguous_and_kind(capsys: pytest.CaptureFixture[str]) -> None:
    refs = [
        TicketRef(provider="gitea", id="1", kind="issue"),
        TicketRef(provider="github", id="2", kind="pull_request", ambiguous=True),
    ]
    _emit_ticket_refs(refs)
    out = capsys.readouterr().out
    assert "gitea#1  (issue)" in out
    assert "github#2  (PR)" in out
    assert "ambiguous" in out
