"""Decor as the LAUNCH actually composes it, through the real manager.

`test_container_decor.py` pins the pure planner and the shipped shell assets.
This module pins the wiring, which is where the two hazards live:

* the ``--settings`` payload a container launch is handed must be the merged
  container file, and a HOST launch must still get the plain hook file — a
  ``statusLine`` naming a path under ``/grove`` is meaningless on the host and
  would silently replace whatever statusline that user configured;
* ``tmux -f`` on a file that is not there fails the start outright, so the
  config path has to come off the provisioned record rather than off config.

Both are producer tests on purpose: the planner being correct proves nothing
about whether anything calls it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from grove.core import paths
from grove.core.config import GroveConfig
from grove.core.container_decor import CONTAINER_DECOR_ROOT, DecorPayload
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import OVERRIDE_CONFIG_RELPATH
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeCli, FakePreflight, FakeTmux


def _cfg(tmp_path: Path, *, container: bool, decor: bool = True) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees")},
            "container": {"enabled": container, "decor": {"enabled": decor}},
        }
    )


def _manager(
    tmp_repo: Path, tmp_path: Path, *, container: bool, decor: bool = True
) -> WorkspaceManager:
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, container=container, decor=decor),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(image_has_tmux=True),
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="ws"))


def _settings_flag(argv: Sequence[str]) -> str | None:
    """The value of the last ``--settings`` in *argv*, or ``None``.

    Last rather than first because that is the occurrence the CLI itself keeps:
    the flag is single-valued, which is the whole reason a container-only key
    has to be merged into one file instead of layered as a second one.
    """
    for index in range(len(argv) - 1, 0, -1):
        if argv[index - 1] == "--settings":
            return argv[index]
    return None


def test_a_container_launch_is_handed_the_merged_settings_file(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The container file carries the statusline; the shared hook file does not.

    Asserted on the CONTENTS of both rendered files rather than on which path
    was passed, because the bug this prevents is a `statusLine` reaching a host
    agent — and that is a property of what is IN the file, not of its name.
    """
    _create(_manager(tmp_repo, tmp_path, container=True))

    container_file = json.loads(paths.agent_container_settings_path().read_text())
    hook_file = json.loads(paths.agent_hooks_settings_path().read_text())

    assert container_file["statusLine"]["type"] == "command"
    assert (
        str(CONTAINER_DECOR_ROOT / DecorPayload.STATUSLINE_NAME)
        in (container_file["statusLine"]["command"])
    )
    # The shared file is what a HOST launch is handed, so a statusline here
    # would follow the user into every session Grove did not containerize.
    assert "statusLine" not in hook_file
    # Everything else is the same payload — the container file is the hook file
    # plus one key, never a second declaration of the hooks themselves.
    assert container_file["hooks"] == hook_file["hooks"]


def test_a_host_launch_is_handed_the_plain_hook_file(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The discriminator is the share plan, which is ``None`` on the host."""
    _create(_manager(tmp_repo, tmp_path, container=False))

    _, decoration = fake_tmux.launch_decorations[-1]
    passed = _settings_flag(decoration)
    assert passed is not None
    assert passed == str(paths.agent_hooks_settings_path())
    assert "statusLine" not in json.loads(Path(passed).read_text(encoding="utf-8"))


def test_the_agent_launch_names_the_decor_config_it_provisioned(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """`tmux -f` is composed only for a config the provision actually recorded."""
    state = _create(_manager(tmp_repo, tmp_path, container=True))

    assert state.container is not None
    assert state.container.tmux_conf == str(CONTAINER_DECOR_ROOT / DecorPayload.TMUX_CONF_NAME)


def test_decor_off_records_no_config_and_composes_no_flag(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The whole feature is one switch away from absent, mount included.

    Both halves are asserted because they are gated separately and either one
    left live is its own defect: an empty `tmux_conf` is precisely what makes
    `TmuxEntry` emit no `-f` (so this covers the agent session, the shell
    session and any added agent at once), while the override config is the only
    place that says whether Grove put a read-only directory of its own inside
    somebody else's image for nothing.
    """
    state = _create(_manager(tmp_repo, tmp_path, container=True, decor=False))

    assert state.container is not None
    assert state.container.tmux_conf == ""
    override = json.loads(
        (Path(state.worktree_path) / OVERRIDE_CONFIG_RELPATH).read_text(encoding="utf-8")
    )
    assert not any("/grove/decor" in mount for mount in override.get("mounts", []))


def test_switching_off_every_consumer_switches_off_the_mount(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """A payload nothing reads must not be bound into somebody else's image.

    The failure this prevents is not a broken workspace, it is a live-looking
    one: a read-only `/grove/decor` with no consumer reads as a feature to
    whoever finds it. The mount must be gated on the same switch as the build,
    not the build alone.
    """
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=GroveConfig.model_validate(
            {
                "worktree": {"root_template": str(tmp_path / "trees")},
                "container": {
                    "enabled": True,
                    "decor": {"statusline": False, "tmux_conf": False},
                },
            }
        ),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(image_has_tmux=True),
        preflight=FakePreflight(),
    )
    state = _create(manager)

    assert state.container is not None
    assert state.container.tmux_conf == ""
    override = json.loads(
        (Path(state.worktree_path) / OVERRIDE_CONFIG_RELPATH).read_text(encoding="utf-8")
    )
    assert not any("/grove/decor" in mount for mount in override.get("mounts", []))
