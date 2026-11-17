"""Create, clone and adopt a project: refuse before writing, clean up only what we made."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from grove.core.contracts.projects import (
    AddProjectRequest,
    AdoptProject,
    CloneProject,
    CreateProject,
)
from grove.core.errors import GitError, ProjectTargetRefused
from grove.core.project_onboarding import ProjectOnboarding

_REQUEST: TypeAdapter[AddProjectRequest] = TypeAdapter(AddProjectRequest)


@pytest.fixture
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """`init_with_empty_commit` commits as the user; give the test process one."""
    for key, value in {
        "GIT_AUTHOR_NAME": "Grove Test",
        "GIT_AUTHOR_EMAIL": "test@grove.local",
        "GIT_COMMITTER_NAME": "Grove Test",
        "GIT_COMMITTER_EMAIL": "test@grove.local",
    }.items():
        monkeypatch.setenv(key, value)


def _registered(tmp_state_dir: Path) -> list[str]:
    config = tmp_state_dir.parent / "config" / "config.json"
    return (
        json.loads(config.read_text(encoding="utf-8")).get("projects", [])
        if config.exists()
        else []
    )


def test_create_makes_a_repo_with_a_head_and_registers_it(
    tmp_path: Path, tmp_state_dir: Path, git_identity: None
) -> None:
    """A fresh `git init` has no HEAD, and `worktree add` needs one, so the
    empty initial commit is what makes the project usable for a workspace."""
    root = tmp_path / "Projects"
    made = ProjectOnboarding(str(root)).add(CreateProject(name="weather-bot"))
    assert made == (root / "weather-bot").resolve()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=made, capture_output=True, text=True, check=False
    )
    assert head.returncode == 0, head.stderr
    assert _registered(tmp_state_dir) == [str(made)]


def test_create_refuses_an_existing_directory_and_writes_nothing(
    tmp_path: Path, tmp_state_dir: Path
) -> None:
    root = tmp_path / "Projects"
    (root / "taken").mkdir(parents=True)
    (root / "taken" / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ProjectTargetRefused, match="already exists"):
        ProjectOnboarding(str(root)).add(CreateProject(name="taken"))
    assert not (root / "taken" / ".git").exists()
    assert (root / "taken" / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert _registered(tmp_state_dir) == []


def test_a_relative_location_is_refused(tmp_path: Path, tmp_state_dir: Path) -> None:
    """A relative path would resolve against the daemon's own cwd, which no
    user chose, so it is refused rather than interpreted."""
    with pytest.raises(ProjectTargetRefused, match="absolute"):
        ProjectOnboarding(str(tmp_path)).add(CreateProject(name="x", location="elsewhere"))


@pytest.mark.parametrize("name", ["../escape", "a/b", "-rf", ".hidden", ""])
def test_a_name_that_is_not_one_safe_segment_never_validates(name: str) -> None:
    with pytest.raises(ValidationError):
        CreateProject(name=name)


def test_a_flag_shaped_url_never_validates() -> None:
    with pytest.raises(ValidationError):
        CloneProject(url="--upload-pack=touch /tmp/pwned")


def test_the_union_dispatches_on_kind() -> None:
    assert isinstance(_REQUEST.validate_python({"kind": "adopt", "path": "/x"}), AdoptProject)
    with pytest.raises(ValidationError):
        _REQUEST.validate_python({"kind": "clone"})


def test_clone_lands_under_the_root_with_the_url_derived_name(
    tmp_path: Path, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    root = tmp_path / "Projects"
    made = ProjectOnboarding(str(root)).add(CloneProject(url=str(tmp_repo) + "/.git"))
    assert made == (root / "repo").resolve()
    assert (made / "README.md").read_text(encoding="utf-8") == "test\n"
    assert _registered(tmp_state_dir) == [str(made)]


def test_a_failed_clone_leaves_no_directory_and_no_registration(
    tmp_path: Path, tmp_state_dir: Path
) -> None:
    root = tmp_path / "Projects"
    with pytest.raises(GitError):
        ProjectOnboarding(str(root)).add(
            CloneProject(url=str(tmp_path / "missing.git"), name="ghost")
        )
    assert not (root / "ghost").exists()
    assert _registered(tmp_state_dir) == []


def test_a_clone_killed_mid_transfer_leaves_no_partial_directory(
    tmp_path: Path, tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout kills git before it can remove its own partial directory, so
    Grove's cleanup is the only thing that does. Simulated at the git seam: the
    double writes the directory a half-finished clone leaves, then fails."""

    def _killed_clone(url: str, dest: Path) -> None:
        (dest / ".git").mkdir(parents=True)
        raise GitError(f"`git clone -- {url}` timed out after 1800s and was killed")

    monkeypatch.setattr("grove.core.project_onboarding.GitRepo.clone", _killed_clone)
    root = tmp_path / "Projects"
    with pytest.raises(GitError, match="timed out"):
        ProjectOnboarding(str(root)).add(
            CloneProject(url="https://git.example.com/acme/huge.git", name="huge")
        )
    assert not (root / "huge").exists()
    assert _registered(tmp_state_dir) == []


def test_a_failed_clone_never_deletes_a_directory_it_did_not_make(
    tmp_path: Path, tmp_state_dir: Path
) -> None:
    """The cleanup arm must be unreachable for a pre-existing target: the
    refusal fires first, so the user's directory is never handed to rmtree."""
    root = tmp_path / "Projects"
    (root / "ghost").mkdir(parents=True)
    (root / "ghost" / "data").write_text("precious", encoding="utf-8")
    with pytest.raises(ProjectTargetRefused):
        ProjectOnboarding(str(root)).add(
            CloneProject(url=str(tmp_path / "missing.git"), name="ghost")
        )
    assert (root / "ghost" / "data").read_text(encoding="utf-8") == "precious"


def test_clone_failure_message_hides_url_credentials() -> None:
    raw = "fatal: repository 'https://bob:hunter2@git.example.com/acme/widgets.git/' not found"
    assert "hunter2" not in ProjectOnboarding.redact(raw)
    assert "https://***@git.example.com/acme/widgets.git/" in ProjectOnboarding.redact(raw)


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("git@git.example.com:acme/widgets.git", "widgets"),
        ("https://git.example.com/acme/widgets", "widgets"),
        ("https://git.example.com/acme/widgets.git/", "widgets"),
    ],
)
def test_name_from_url_matches_what_git_clone_would_pick(url: str, name: str) -> None:
    assert ProjectOnboarding.name_from_url(url) == name


def test_adopt_registers_the_enclosing_repo_root(
    tmp_path: Path, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    sub = tmp_repo / "src"
    sub.mkdir()
    made = ProjectOnboarding(str(tmp_path)).add(AdoptProject(path=str(sub)))
    assert made == tmp_repo
    assert _registered(tmp_state_dir) == [str(tmp_repo)]


def test_adopt_refuses_a_directory_outside_any_repo(tmp_path: Path, tmp_state_dir: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ProjectTargetRefused, match="not inside a git repository"):
        ProjectOnboarding(str(tmp_path)).add(AdoptProject(path=str(plain)))
    assert _registered(tmp_state_dir) == []


def test_candidates_list_unregistered_repos_only(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    for name in ("alpha", "beta", "gamma"):
        (root / name).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root / name)], check=True)
    (root / "not-a-repo").mkdir()
    subprocess.run(
        ["git", "remote", "add", "origin", "git@git.example.com:acme/beta.git"],
        cwd=root / "beta",
        check=True,
    )
    view = ProjectOnboarding(str(root)).candidates({(root / "alpha").resolve()})
    assert view.root == str(root.resolve())
    assert [c.name for c in view.candidates] == ["beta", "gamma"]
    assert view.candidates[0].remote == "git@git.example.com:acme/beta.git"
    assert view.candidates[1].remote is None


def test_candidates_for_a_missing_root_is_empty_not_an_error(tmp_path: Path) -> None:
    view = ProjectOnboarding(str(tmp_path / "nowhere")).candidates(set())
    assert view.candidates == ()
