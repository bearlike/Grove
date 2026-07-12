"""`add_known_project` — the read-modify-write mutator behind `grove config add-project`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core.config import add_known_project
from grove.core.errors import ConfigError


def test_add_known_project_creates_file_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "config" / "config.json"
    repo = tmp_path / "repo"
    repo.mkdir()

    added = add_known_project(repo, target=target)

    assert added is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["projects"] == [str(repo.resolve())]


def test_add_known_project_appends_to_existing_projects(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    other = tmp_path / "other"
    other.mkdir()
    payload = {"projects": [str(other)], "ui": {"theme": "dark"}}
    target.write_text(json.dumps(payload), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    added = add_known_project(repo, target=target)

    assert added is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data["projects"]) == {str(other), str(repo.resolve())}
    assert data["ui"] == {"theme": "dark"}  # untouched sibling keys survive the read-modify-write


def test_add_known_project_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    repo = tmp_path / "repo"
    repo.mkdir()

    first = add_known_project(repo, target=target)
    second = add_known_project(repo, target=target)

    assert first is True
    assert second is False
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["projects"] == [str(repo.resolve())]


def test_add_known_project_dedupes_unresolved_paths(tmp_path: Path) -> None:
    """A `projects` entry written as `~`-relative or unresolved still dedupes."""
    target = tmp_path / "config.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    target.write_text(json.dumps({"projects": [f"{repo}/./"]}), encoding="utf-8")

    added = add_known_project(repo, target=target)

    assert added is False


def test_add_known_project_rejects_non_list_projects_field(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"projects": "not-a-list"}), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ConfigError):
        add_known_project(repo, target=target)
