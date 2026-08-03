"""`AgentRoster` + `GroveConfig.builtin_agents`: the opt-out knob on the
built-in-seed contract.

Split from `test_config_cascade.py`: that file exercises the general cascade
machinery (deep-merge, `_merge_agents`, env parsing, unrelated sections);
this one is scoped to the one new concern — the `builtin_agents` gate — and
its most important case (case 3 below) reaches into the `WorkspaceManager`
create path, a different fixture surface than the rest of that file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import AgentRoster, _parse_env_overrides, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ConfigError, GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

# ─── default is unchanged ────────────────────────────────────────────────────


def test_default_roster_is_exactly_the_builtins(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """With zero config files, `builtin_agents` defaults `True` and the roster
    is exactly the three built-ins. Adding the opt-out knob must not change
    the default first-run experience."""
    del tmp_state_dir
    cfg = load_config(tmp_repo, env={})
    assert cfg.builtin_agents is True
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}


# ─── builtin_agents: false ───────────────────────────────────────────────────


def test_builtin_agents_false_with_custom_agent_only_hides_builtins(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """`builtin_agents: false` plus one custom agent leaves exactly that agent
    — the built-ins are gone, not merely unlisted."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps(
            {
                "builtin_agents": False,
                "agents": [{"name": "aider", "command": "aider", "kind": "generic"}],
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"aider"}
    aider = cfg.find_agent("aider")
    assert aider is not None
    assert aider.command == "aider"


def test_builtin_agents_false_reinstates_full_spec_by_bare_name(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The most important test in this file.

    `builtin_agents: false` combined with a BARE `{"name": "claude"}` must
    still produce a `claude` agent with its full built-in spec intact
    (`kind == "claude_code"`, `command == "claude"`). This pins that the seed
    layer (layer 0 of the cascade) keeps merging even when the gate is about
    to filter its result down to the declared names — if the seed were ever
    skipped instead for `builtin_agents: false` (the tempting-but-wrong
    implementation: "false means don't seed"), this same bare entry would
    validate down to `kind == "generic"` and silently kill Activity
    Dashboard tracking for anyone who re-opts a built-in back in.
    """
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"builtin_agents": False, "agents": [{"name": "claude"}]}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"claude"}
    claude = cfg.find_agent("claude")
    assert claude is not None
    assert claude.kind == "claude_code"
    assert claude.command == "claude"


def test_builtin_reinstated_from_a_different_layer_than_the_flag(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The flag and the re-declaration don't need to share a layer.

    `AgentRoster.names_in` unions declared names across every overlay layer,
    and the flag itself is read off the fully merged `cfg` — not per-layer —
    so a user-global `builtin_agents: false` plus a project-local
    `{"name": "codex"}` still reinstates codex."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"builtin_agents": False}), encoding="utf-8")

    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"agents": [{"name": "codex"}]}), encoding="utf-8")

    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"codex"}
    codex = cfg.find_agent("codex")
    assert codex is not None
    assert codex.kind == "codex"
    assert codex.command == "codex"


# ─── the flag itself cascades ────────────────────────────────────────────────


def test_builtin_agents_flag_cascades_via_project_layer(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"builtin_agents": False}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.builtin_agents is False
    assert cfg.agents == []


def test_builtin_agents_flag_cascades_via_env(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    cfg = load_config(tmp_repo, env={"GROVE_BUILTIN_AGENTS": "false"})
    assert cfg.builtin_agents is False
    assert cfg.agents == []


def test_parse_env_overrides_recognizes_bare_top_level_field() -> None:
    """`builtin_agents` is a bare top-level bool, not a submodel — unlike every
    other `GROVE_<SECTION>__<FIELD>` example in `test_config_cascade.py`,
    there is no double-underscore nesting here (`parts` is length 1, so the
    `for part in parts[:-1]` loop never runs). Pin it directly: the
    membership test that decides whether a `GROVE_*` var is config is against
    `GroveConfig.model_fields`, not a nesting heuristic, so a bare field name
    is recognized just like a nested one."""
    assert _parse_env_overrides({"GROVE_BUILTIN_AGENTS": "false"}) == {"builtin_agents": "false"}


# ─── empty roster is legal ───────────────────────────────────────────────────


def test_builtin_agents_false_with_nothing_declared_yields_empty_roster_without_raising(
    tmp_state_dir: Path,
) -> None:
    """Empty roster is legal, not an error — deliberate.

    The daemon loads its GLOBAL config with `load_config(repo_root=None)`
    (the user layer alone; every per-repo Manager then resolves its own
    project-scoped cascade separately, see core/CLAUDE.md's registry
    `config_loader` note). A validator here requiring at least one agent
    would break every one of those global loads for an operator who disables
    built-ins globally and declares agents only per-project — the global
    layer alone has none to declare. So `builtin_agents: false` with no
    `agents` declared anywhere must validate to an empty roster and must NOT
    raise.
    """
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"builtin_agents": False}), encoding="utf-8")

    cfg = load_config(repo_root=None, env={})  # must not raise
    assert cfg.agents == []


# ─── the gate is a real gate ─────────────────────────────────────────────────


@pytest.fixture
def hidden_builtin_manager(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> WorkspaceManager:
    """A Manager whose cascade disables built-ins with nothing re-declared —
    the roster the gate must actually enforce at `create()`, not just the
    validated shape `load_config` returns."""
    del tmp_state_dir, fake_tmux
    cfg = load_config(tmp_repo, cli_overrides={"builtin_agents": False}, env={})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_gate_rejects_a_hidden_builtin_agent_at_create(
    hidden_builtin_manager: WorkspaceManager,
) -> None:
    """A hidden built-in is absent from `find_agent`, so `create` rejects it
    with the same "unknown agent" error a nonexistent name gets — the
    validated config shape alone proves nothing; this proves the gate is
    live on the write path a real caller hits."""
    assert hidden_builtin_manager.config.agents == []
    with pytest.raises(GroveError, match="unknown agent"):
        hidden_builtin_manager.create(CreateWorkspaceRequest(agent_name="claude", title="x"))


# ─── malformed layers don't crash the gate ───────────────────────────────────


def test_a_project_config_whose_agent_forgot_its_name_fails_the_load(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """An `agents` entry missing `name` must fail the load loudly rather than
    silently dropping that agent from the roster."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps(
            {
                "agents": [
                    {"name": "claude", "command": "claude --model opus"},
                    {"command": "aider --model sonnet", "description": "Aider"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="invalid 'agents' entry"):
        load_config(tmp_repo, env={})


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("not-a-dict", id="bare-string"),
        pytest.param({"command": "x"}, id="missing-name"),
        pytest.param({"name": 123, "command": "y"}, id="non-string-name"),
    ],
)
def test_a_malformed_agent_entry_is_reported_not_skipped(bad: object) -> None:
    """A malformed `agents` entry must be reported, never silently skipped.

    This pass reads raw JSON *before* `model_validate`, so a malformed entry
    must raise here rather than being skipped on the assumption that
    validation will report it later: skipping keeps the entry away from
    Pydantic entirely, so `extra="forbid"` — the mechanism this module
    promises catches every typo — never fires on it. A custom agent with no
    `name` would otherwise vanish from the roster silently.
    """
    with pytest.raises(ConfigError, match="invalid 'agents' entry"):
        AgentRoster.names_in([{"agents": [bad, {"name": "ok", "command": "z"}]}])


def test_a_well_formed_layer_still_yields_every_declared_name() -> None:
    layers = [
        {"agents": [{"name": "ok", "command": "z"}]},
        {"agents": [{"name": "other"}]},
        {"other_key": 1},
        # A non-list `agents` falls through untouched: Pydantic sees that one and
        # names the field better than this pass could.
        {"agents": "claude"},
    ]
    assert AgentRoster.names_in(layers) == frozenset({"ok", "other"})
