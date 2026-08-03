"""``grove sessions`` Typer subcommand surface.

In-process via CliRunner against a real tmp git repo and a sandboxed Claude
config dir — no HTTP, no real ``~/.claude``. Pins the table/JSON renderings,
the show/dump shapes, and the error paths (outside a repo, unknown ref).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.agents.claude_code import _ClaudeHome
from grove.tui.cli import app

SID = "11111111-1111-4111-8111-111111111111"


def _init_repo(path: Path) -> Path:
    """A second, unrelated real git repo — the `tmp_repo` fixture only ever
    gives one, and the host-scope test needs two independent projects."""
    path.mkdir(parents=True)
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@grove.local"],
        ["git", "config", "user.name", "Grove Test"],
    ):
        subprocess.run(args, cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def _write_transcript(
    claude: Path, repo: Path, session_id: str, prompt: str, *, branch: str = "main"
) -> None:
    folder = claude / "projects" / _ClaudeHome.encode_cwd(repo)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text(
        f'{{"type":"user","uuid":"h-{session_id[:4]}","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{repo}","gitBranch":"{branch}",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (2_000, 2_000))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    tmp_path: Path,
) -> Path:
    """cwd inside a real repo, Claude config sandboxed, one transcript on disk."""
    del tmp_state_dir
    claude = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_repo)

    folder = claude / "projects" / _ClaudeHome.encode_cwd(tmp_repo)
    folder.mkdir(parents=True)
    path = folder / f"{SID}.jsonl"
    path.write_text(
        '{"type":"ai-title","aiTitle":"Sample session"}\n'
        f'{{"type":"user","uuid":"h1","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{tmp_repo}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"do the thing"}}}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1",'
        '"timestamp":"2026-06-09T08:00:05.000Z","isSidechain":false,'
        '"message":{"id":"m1","role":"assistant","model":"claude-opus-4-8",'
        '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}\n',
        encoding="utf-8",
    )
    os.utime(path, (2_000, 2_000))
    return tmp_repo


def test_list_renders_table(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["sessions", "list"])
    assert result.exit_code == 0, result.output
    assert "SESSION" in result.output  # header
    assert SID[:8] in result.output
    assert "Sample session" in result.output


def test_list_json_payload(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["sessions", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    row = payload[0]
    assert row["session_id"] == SID
    assert row["agent"] == "claude_code"
    assert row["cwd"] == str(project)
    assert row["title"] == "Sample session"
    assert row["provenance"] == "fs_discovered"
    assert row["human_turns"] == 1


def test_list_since_filter_excludes_old(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["sessions", "list", "--since", "1d"])
    assert result.exit_code == 0, result.output
    assert "no sessions found" in result.output  # mtime is the 1970 epoch


def test_show_renders_turns_and_json(runner: CliRunner, project: Path) -> None:
    del project
    human = runner.invoke(app, ["sessions", "show", SID[:8]])
    assert human.exit_code == 0, human.output
    assert "do the thing" in human.output
    assert "Done." in human.output

    structured = runner.invoke(app, ["sessions", "show", SID[:8], "--json", "--last", "1"])
    assert structured.exit_code == 0, structured.output
    payload = json.loads(structured.output)
    assert payload["session"]["session_id"] == SID
    assert payload["turns"][0]["user_text"] == "do the thing"
    assert payload["turns"][0]["entries"][-1]["text"] == "Done."


def test_dump_default_and_jsonl(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["sessions", "dump", SID[:8]])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["session_id"] == SID
    assert len(payload["files"]) == 1
    assert payload["files"][0]["records"][0]["type"] == "ai-title"

    raw = runner.invoke(app, ["sessions", "dump", SID[:8], "--jsonl"])
    assert raw.exit_code == 0, raw.output
    lines = [line for line in raw.output.splitlines() if line.strip()]
    assert len(lines) == 3
    assert json.loads(lines[0])["type"] == "ai-title"


def test_unknown_ref_fails_loudly(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["sessions", "show", "deadbeef"])
    assert result.exit_code == 1
    assert "no session" in result.output


def test_outside_a_repo_fails_loudly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_state_dir: Path
) -> None:
    del tmp_state_dir
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    result = runner.invoke(app, ["sessions", "list"])
    assert result.exit_code == 1
    assert "not inside a git repository" in result.output


def test_list_host_scope_includes_repos_outside_cfg_projects(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    """``--host`` widens `grove sessions list` to the whole host: a second,
    completely unrelated repo — never declared in `cfg.projects`, no Grove
    workspace, not an ancestor/descendant of the cwd — still surfaces, with
    the host-only PROJECT/BRANCH/LIVE columns. Project scope stays exactly
    as narrow as before."""
    del tmp_state_dir
    claude = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repo_a = _init_repo(tmp_path / "repo-a")
    repo_b = _init_repo(tmp_path / "repo-b")

    sid_a = "22222222-2222-4222-8222-222222222222"
    sid_b = "33333333-3333-4333-8333-333333333333"
    _write_transcript(claude, repo_a, sid_a, "work in repo a", branch="main")
    _write_transcript(claude, repo_b, sid_b, "work in repo b", branch="feature/b")
    monkeypatch.chdir(repo_a)

    project_result = runner.invoke(app, ["sessions", "list", "--json"])
    assert project_result.exit_code == 0, project_result.output
    project_ids = {row["session_id"] for row in json.loads(project_result.output)}
    assert project_ids == {sid_a}

    host_result = runner.invoke(app, ["sessions", "list", "--host", "--json"])
    assert host_result.exit_code == 0, host_result.output
    payload = json.loads(host_result.output)
    ids = {row["session_id"] for row in payload}
    assert {sid_a, sid_b} <= ids
    row_b = next(row for row in payload if row["session_id"] == sid_b)
    assert row_b["project"] == "repo-b"
    assert row_b["git_branch"] == "feature/b"
    assert row_b["workspace_id"] is None
    assert row_b["is_grove_managed"] is False
    assert row_b["live"] is False

    table_result = runner.invoke(app, ["sessions", "list", "--host"])
    assert table_result.exit_code == 0, table_result.output
    assert "PROJECT" in table_result.output
    assert "BRANCH" in table_result.output
    assert "LIVE" in table_result.output
    assert "repo-b" in table_result.output
    assert "feature/b" in table_result.output
