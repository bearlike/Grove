"""Runtime-aware default agent profiles and their load-time validation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ConfigError
from grove.core.launch_resolution import LaunchResolver
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux


def _cfg(tmp_path: Path, **defaults: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [
                {"name": "claude-host", "command": "claude", "kind": "claude_code"},
                {"name": "claude-container", "command": "claude", "kind": "claude_code"},
                {"name": "codex", "command": "codex", "kind": "codex"},
            ],
            "defaults": defaults,
        }
    )


def test_an_agent_kind_uses_the_runtime_specific_profile_before_the_wildcard(
    tmp_path: Path,
) -> None:
    """Deleting the runtime-row lookup would launch the wildcard profile on a container."""
    cfg = _cfg(
        tmp_path,
        profiles={
            "*": {"claude_code": "claude-host"},
            "container": {"claude_code": "claude-container"},
        },
    )

    launch = LaunchResolver.resolve(
        cfg,
        CreateWorkspaceRequest(agent_kind="claude_code", runtime=Runtime.CONTAINER),
    )

    assert launch.agent is not None
    assert launch.agent.name == "claude-container"
    assert launch.sources["agent_name"] == "config"
    assert [(item.field, item.value) for item in launch.adjustments] == [
        ("agent_name", "claude-container")
    ]


def test_an_agent_kind_falls_back_to_the_wildcard_profile(tmp_path: Path) -> None:
    """Deleting the wildcard lookup would pick the roster's first profile instead."""
    cfg = _cfg(tmp_path, profiles={"*": {"claude_code": "claude-container"}})

    launch = LaunchResolver.resolve(cfg, CreateWorkspaceRequest(agent_kind="claude_code"))

    assert launch.agent is not None
    assert launch.agent.name == "claude-container"
    assert launch.sources["agent_name"] == "config"


def test_an_explicit_profile_still_beats_a_runtime_profile(tmp_path: Path) -> None:
    """Replacing an explicit request from the matrix would discard the person's choice."""
    cfg = _cfg(tmp_path, profiles={"host": {"claude_code": "claude-host"}})

    launch = LaunchResolver.resolve(
        cfg,
        CreateWorkspaceRequest(agent_name="claude-container", runtime=Runtime.HOST),
    )

    assert launch.agent is not None
    assert launch.agent.name == "claude-container"
    assert launch.sources["agent_name"] == "request"
    assert launch.adjustments == ()


def test_a_named_remote_block_beats_the_remote_kind_profile(tmp_path: Path) -> None:
    """Dropping the named-row tier would launch the generic remote profile on this block."""
    cfg = GroveConfig.model_validate(
        {
            "agents": [
                {"name": "claude-remote", "command": "claude", "kind": "claude_code"},
                {"name": "claude-sandbox", "command": "claude", "kind": "claude_code"},
            ],
            "runtimes": {
                "sandbox": {
                    "kind": "opensandbox",
                    "image": "example:latest",
                    "resources": {"cpu": "1"},
                }
            },
            "defaults": {
                "profiles": {
                    "remote": {"claude_code": "claude-remote"},
                    "sandbox": {"claude_code": "claude-sandbox"},
                }
            },
        }
    )

    launch = LaunchResolver.resolve(
        cfg,
        CreateWorkspaceRequest(
            agent_kind="claude_code", runtime=Runtime.REMOTE, remote_runtime="sandbox"
        ),
    )

    assert launch.agent is not None and launch.agent.name == "claude-sandbox"
    assert launch.sources["agent_name"] == "config"


def test_profile_and_default_agent_ties_follow_their_saving_layers(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Collapsing provenance to the merged map would make lower-layer defaults win ties."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    _write(user, {"defaults": {"agent": "claude-host"}})
    project = paths_mod.project_config_path(tmp_repo)
    _write(
        project,
        {
            "agents": [
                {"name": "claude-host", "command": "claude", "kind": "claude_code"},
                {"name": "claude-container", "command": "claude", "kind": "claude_code"},
            ],
            "defaults": {"profiles": {"container": {"claude_code": "claude-container"}}},
        },
    )
    specific = LaunchResolver.resolve(
        load_config(tmp_repo, env={}),
        CreateWorkspaceRequest(agent_kind="claude_code", runtime=Runtime.CONTAINER),
    )

    _write(user, {"defaults": {"profiles": {"*": {"claude_code": "claude-container"}}}})
    _write(
        project,
        {
            "agents": [
                {"name": "claude-host", "command": "claude", "kind": "claude_code"},
                {"name": "claude-container", "command": "claude", "kind": "claude_code"},
            ],
            "defaults": {"agent": "claude-host"},
        },
    )
    default = LaunchResolver.resolve(
        load_config(tmp_repo, env={}), CreateWorkspaceRequest(agent_kind="claude_code")
    )

    assert specific.agent is not None and specific.agent.name == "claude-container"
    assert specific.sources["agent_name"] == "project"
    assert default.agent is not None and default.agent.name == "claude-host"
    assert default.sources["agent_name"] == "project"


def test_one_concrete_profile_row_infers_an_omitted_runtime(tmp_path: Path) -> None:
    """Deleting reverse selection would leave this one unambiguous runtime on the host."""
    cfg = _cfg(tmp_path, profiles={"container": {"claude_code": "claude-host"}})

    launch = LaunchResolver.resolve(cfg, CreateWorkspaceRequest(agent_kind="claude_code"))

    assert launch.runtime is Runtime.CONTAINER
    assert launch.sources["runtime"] == "config"


def test_ambiguous_profile_rows_do_not_infer_a_runtime_or_beat_a_saved_one(tmp_path: Path) -> None:
    """Choosing either of two matching rows would make a matrix order decide a runtime."""
    profiles = {
        "host": {"claude_code": "claude-host"},
        "container": {"claude_code": "claude-host"},
    }

    ambiguous = LaunchResolver.resolve(_cfg(tmp_path, profiles=profiles), CreateWorkspaceRequest())
    saved = LaunchResolver.resolve(
        _cfg(tmp_path, runtime="host", profiles={"container": {"claude_code": "claude-host"}}),
        CreateWorkspaceRequest(),
    )

    assert ambiguous.sources["runtime"] == "builtin"
    assert saved.runtime is Runtime.HOST
    assert saved.sources["runtime"] == "config"


def test_an_explicit_profile_cannot_infer_another_tools_runtime(tmp_path: Path) -> None:
    """Using the roster default's kind here would move an explicit codex launch to container."""
    cfg = _cfg(
        tmp_path,
        agent="claude-host",
        profiles={"container": {"claude_code": "claude-host"}},
    )

    launch = LaunchResolver.resolve(cfg, CreateWorkspaceRequest(agent_name="codex"))

    assert launch.sources["runtime"] == "builtin"


def test_container_fallback_reresolves_the_host_profile_for_preview_and_create(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Keeping the container profile after fallback would launch the wrong host profile."""
    cfg = _cfg(
        tmp_path,
        agent="claude-container",
        model="model-for-container",
        runtime="container",
        profiles={
            "*": {"claude_code": "claude-container"},
            "host": {"claude_code": "claude-host"},
            "container": {"claude_code": "claude-container"},
        },
    )
    cli = FakeCli()
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(ready=False, devcontainer_cli=cli),  # type: ignore[arg-type]
    )
    request = CreateWorkspaceRequest(title="fallback")

    preview = manager.preview(request, container_unavailable="docker is down")
    state = manager.create(request)

    assert preview.launch.agent is not None
    assert preview.launch.agent.name == state.agent_name == "claude-host"
    assert preview.launch.model is None
    assert "--model" not in dict(fake_tmux.launch_decorations)[state.tmux_session]
    assert preview.runtime is state.runtime is Runtime.HOST
    assert [(item.field, item.value) for item in preview.adjustments] == [
        ("runtime", "host"),
        ("agent_name", "claude-host"),
    ]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("defaults", "path"),
    [
        ({"agent": "missing"}, "defaults.agent"),
        ({"profiles": {"missing": {"claude_code": "claude"}}}, "defaults.profiles.missing"),
        (
            {"profiles": {"remote": {"claude_code": "shell"}}},
            "defaults.profiles.remote.claude_code",
        ),
        ({"profiles": {"*": {"claude_code": "missing"}}}, "defaults.profiles.*.claude_code"),
        ({"profiles": {"*": {"claude_code": "codex"}}}, "defaults.profiles.*.claude_code"),
    ],
)
def test_invalid_saved_profile_defaults_name_their_bad_key(
    defaults: dict[str, object], path: str, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Removing each load guard would admit a saved launch answer that cannot run."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    _write(
        project,
        {
            "agents": [
                {"name": "claude", "command": "claude", "kind": "claude_code"},
                {"name": "codex", "command": "codex", "kind": "codex"},
                {"name": "shell", "command": "sh", "kind": "generic"},
            ],
            "defaults": defaults,
        },
    )

    with pytest.raises(ConfigError, match=re.escape(path)):
        load_config(tmp_repo, env={})


def test_an_episodic_runtime_cannot_have_a_profile_row(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """Allowing a task-only row would promise a profile for a runtime that has no resident agent."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    _write(
        project,
        {
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
            "runtimes": {
                "turn": {
                    "kind": "cloud_run_episodic",
                    "project": "demo",
                    "region": "region",
                    "service": "service",
                    "url": "https://example.invalid",
                    "image": "example:latest",
                }
            },
            "defaults": {"profiles": {"turn": {"claude_code": "claude"}}},
        },
    )

    with pytest.raises(ConfigError, match=re.escape("defaults.profiles.turn")):
        load_config(tmp_repo, env={})


def test_a_wildcard_profile_and_default_agent_cannot_disagree_in_one_layer(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A merged map loses this one-file contradiction, so load must inspect the raw layer."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    _write(
        project,
        {
            "agents": [
                {"name": "claude", "command": "claude", "kind": "claude_code"},
                {"name": "claude-alt", "command": "claude", "kind": "claude_code"},
            ],
            "defaults": {
                "agent": "claude",
                "profiles": {"*": {"claude_code": "claude-alt"}},
            },
        },
    )

    with pytest.raises(ConfigError, match=re.escape("defaults.profiles.*.claude_code")):
        load_config(tmp_repo, env={})


def test_profile_cells_merge_without_erasing_another_runtime_row(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Replacing rather than deep-merging profiles would lose the user's wildcard cell."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    _write(user, {"defaults": {"profiles": {"*": {"claude_code": "claude"}}}})
    project = paths_mod.project_config_path(tmp_repo)
    _write(
        project,
        {
            "agents": [
                {"name": "claude", "command": "claude", "kind": "claude_code"},
                {"name": "claude-container", "command": "claude", "kind": "claude_code"},
            ],
            "defaults": {"profiles": {"container": {"claude_code": "claude-container"}}},
        },
    )

    cfg = load_config(tmp_repo, env={})

    assert cfg.defaults.profiles == {
        "*": {"claude_code": "claude"},
        "container": {"claude_code": "claude-container"},
    }
