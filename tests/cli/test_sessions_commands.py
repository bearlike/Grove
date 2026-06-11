"""``grove sessions`` Typer subcommand surface.

In-process via CliRunner against a real tmp git repo and a sandboxed Claude
config dir — no HTTP, no real ``~/.claude``. Pins the table/JSON renderings,
the show/dump shapes, and the error paths (outside a repo, unknown ref).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.agents.claude_code import _ClaudeHome
from grove.tui.cli import app

SID = "11111111-1111-4111-8111-111111111111"


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
