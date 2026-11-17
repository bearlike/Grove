"""What the ``start`` trigger tells a new agent, rendered by the engine.

The first-turn brief used to ride two roads (a Claude Code hook and a prepended
initial prompt) and is now one trigger rule delivered over the same channel as
every other daemon instruction. What survives here is the CONTENT contract:
the rule's text plus the operator's ``brief.instructions`` plus the workspace's
own record, fenced as Grove's voice. The delivery and cap rules live in
``test_triggers.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core.config import GroveConfig, TriggersConfig
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager, build
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceTitle
from tests.conftest import FakeCli, FakePreflight, FakeTmux

FULL_ID = "d" * 64
START = next(
    rule
    for rule in TriggersConfig.model_validate(TriggersConfig.seed_layer()["triggers"]).rules
    if rule.name == "start"
)


def _cfg(tmp_path: Path, **over: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
            **over,
        }
    )


@pytest.fixture
def manager(tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux) -> WorkspaceManager:
    del fake_tmux  # installed via monkeypatch
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )


def _brief(manager: WorkspaceManager, state) -> str:  # type: ignore[no-untyped-def]
    return manager.trigger_text(state, START, START.text)


def test_the_brief_carries_this_workspace_s_own_specification(
    manager: WorkspaceManager,
) -> None:
    """The facts are read off the record, so the agent is told what Grove
    BELIEVES about it — which is what every dashboard and ticket comment is
    also reading. A workspace with no attached tickets says nothing about
    tickets rather than saying "none"."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="specified", description="A real one.")
    )

    text = _brief(manager, state)

    assert text.startswith('<grove-instruction kind="workspace">')
    assert text.endswith("</grove-instruction>")
    assert "working-in-grove" in text
    assert f"- id: {state.id}" in text
    assert "- title: specified" in text
    assert "- description: A real one." in text
    assert f"- branch: {state.branch}" in text
    assert "- runtime: host" in text
    assert "- placement: a dedicated worktree at" in text
    assert "attached tickets" not in text


def test_a_root_placement_workspace_says_so_in_words(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The whole reason the specification exists: "root" alone reads as a
    directory-layout detail, so the value is a sentence that names the
    conclusion the agent was otherwise drawing wrongly."""
    del fake_tmux
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )
    state = mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title="in the root", branch_plan=RootBranch())
    )

    text = _brief(mgr, state)

    assert "the repository root itself" in text
    assert "even though the path looks like an ordinary checkout" in text


def _write_project_config(repo: Path, payload: dict[str, object]) -> None:
    grove_dir = repo / ".grove"
    grove_dir.mkdir(exist_ok=True)
    (grove_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_operator_instructions_follow_groves_text_from_the_repo_cascade(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The per-repo cascade reaches the rendered brief, after Grove's own text."""
    del tmp_state_dir, fake_tmux
    instructions = "Run the project gate before pushing."
    _write_project_config(
        tmp_repo, {"brief": {"instructions": instructions}, "container": {"enabled": False}}
    )
    mgr = build(tmp_repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="cascaded"))

    text = _brief(mgr, state)

    assert instructions in text
    assert text.index("working-in-grove") < text.index(instructions)


def test_a_reminder_rule_is_fenced_as_a_reminder_with_no_facts(manager: WorkspaceManager) -> None:
    """Only the start rule carries the record; a reminder stays one short block."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet"))
    phase = next(
        rule
        for rule in TriggersConfig.model_validate(TriggersConfig.seed_layer()["triggers"]).rules
        if rule.name == "phase"
    )

    text = manager.trigger_text(state, phase, "Report your phase.")

    assert text == '<grove-instruction kind="reminder">\nReport your phase.\n</grove-instruction>'


def test_the_brief_is_never_folded_into_the_launch_prompt(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Delivery is the trigger's job now, so the create prompt rides unchanged."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="plain", initial_prompt="Fix the parser.")
    )
    decoration = next(
        d for session, d in fake_tmux.launch_decorations if session == state.tmux_session
    )
    assert decoration[-1] == "Fix the parser."  # the prompt rides the launch verbatim
    assert not any("grove-instruction" in token for token in decoration)
    assert state.brief is True


def test_brief_stays_a_persisted_per_workspace_choice(manager: WorkspaceManager) -> None:
    quiet = manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet", brief=False))
    assert quiet.brief is False


def test_an_untitled_create_is_named_by_the_engine_never_by_the_prompt(
    manager: WorkspaceManager,
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", initial_prompt="Build the landing page")
    )
    assert WorkspaceTitle.is_generated(state.title)
    assert "landing" not in state.tmux_session
