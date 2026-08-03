"""Autonomy is the product: the trust rule on `share`, and no permission gate.

Two contracts that must hold together. A committed layer may TIGHTEN sharing but
never raise it (a repo cannot grant itself the host's credentials), and Grove
inserts NO permission gate anywhere — a relaxed-permissions agent
(`--dangerously-skip-permissions`, Codex full-auto) is exactly the workflow
containerization exists to enable, so its flags reach the agent untouched.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import ClassVar

import pytest

import grove
from grove.core import paths as paths_mod
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.launch import HostNamespaceBackend, LaunchSpec
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

# ─── committed layers may only tighten ──────────────────────────────────────


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _share(repo: Path) -> str:
    return load_config(repo, env={}).container.agent_config.share


def _committed(repo: Path, share: str) -> None:
    _write(paths_mod.project_config_path(repo), {"container": {"agent_config": {"share": share}}})


def _user(share: str) -> None:
    _write(paths_mod.user_config_path(), {"container": {"agent_config": {"share": share}}})


@pytest.mark.usefixtures("tmp_state_dir")
def test_committed_layer_may_tighten_sharing(tmp_path: Path) -> None:
    """A repo asking to be run isolated is asking for LESS — always honored."""
    _committed(tmp_path, "isolated")

    assert _share(tmp_path) == "isolated"


@pytest.mark.usefixtures("tmp_state_dir")
def test_committed_tightening_wins_over_a_looser_user_layer(tmp_path: Path) -> None:
    """Direction, not precedence: the user's `full` must not undo the repo's floor."""
    _user("full")
    _committed(tmp_path, "isolated")

    assert _share(tmp_path) == "isolated"


@pytest.mark.usefixtures("tmp_state_dir")
def test_committed_layer_cannot_raise_sharing(tmp_path: Path) -> None:
    """The untrusted-input rule: a repo may not grant itself host credentials."""
    _user("isolated")
    _committed(tmp_path, "full")

    assert _share(tmp_path) == "isolated"


@pytest.mark.usefixtures("tmp_state_dir")
def test_committed_layer_cannot_raise_above_the_default_either(tmp_path: Path) -> None:
    """No user layer at all: `full` is already the effective value, so this is a no-op."""
    _committed(tmp_path, "projects")
    assert _share(tmp_path) == "projects"

    _committed(tmp_path, "full")
    assert _share(tmp_path) == "full"


@pytest.mark.usefixtures("tmp_state_dir")
def test_non_committed_local_layer_grants_sharing_when_no_committed_floor(tmp_path: Path) -> None:
    """`.grove/config.local.json` is gitignored — a real grant, not a request."""
    _user("isolated")
    _write(
        paths_mod.project_local_config_path(tmp_path),
        {"container": {"agent_config": {"share": "full"}}},
    )

    assert _share(tmp_path) == "full"


@pytest.mark.usefixtures("tmp_state_dir")
def test_a_committed_floor_is_not_undone_by_any_later_layer(tmp_path: Path) -> None:
    """ "Tighten" means FORCE: the floor holds against every looser layer.

    The asymmetry is the point — raising is a grant (committed layers cannot make
    one), lowering is a restriction (anyone may). A machine that genuinely wants
    more sharing than the repo asks for edits the repo's own config; it is not an
    override a stray local layer should win by accident.
    """
    _committed(tmp_path, "isolated")
    _write(
        paths_mod.project_local_config_path(tmp_path),
        {"container": {"agent_config": {"share": "full"}}},
    )

    assert _share(tmp_path) == "isolated"


@pytest.mark.usefixtures("tmp_state_dir")
def test_committed_layer_keeps_its_other_container_settings(tmp_path: Path) -> None:
    """Only `share` is direction-limited; the rest of the layer merges normally."""
    _write(
        paths_mod.project_config_path(tmp_path),
        {"container": {"agent_config": {"share": "full"}, "docker_bin": "podman"}},
    )

    cfg = load_config(tmp_path, env={})

    assert cfg.container.docker_bin == "podman"
    assert cfg.container.agent_config.share == "full"


# ─── no permission gate anywhere ────────────────────────────────────────────

_RELAXED = "--dangerously-skip-permissions"

#: Strings that would indicate Grove reasoning about an agent's own permission
#: posture. Flags belong to the agent's provider surface; Grove passes argv.
_GATE_TOKENS = (
    "dangerously-skip-permissions",
    "--full-auto",
    "bypassPermissions",
    "acceptEdits",
)


class _CapturingBackend(HostNamespaceBackend):
    """Captures the LaunchSpec instead of touching tmux."""

    provides_pane: ClassVar[bool] = True

    def __init__(self) -> None:
        self.specs: list[LaunchSpec] = []

    def launch(self, spec: LaunchSpec) -> None:
        self.specs.append(spec)


def test_relaxed_permission_flags_reach_the_agent_untouched(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The acceptance criterion: Grove inserts no gate on an autonomous agent.

    The container is the safety mechanism *instead of* prompts, so an agent
    configured to skip them must launch exactly as configured — no filtering, no
    warning-shaped rewrite, no refusal, in host or container mode alike.
    """
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"agent_config": {"share": "full"}, "egress": {"mode": "open"}},
            "agents": [
                {
                    "name": "yolo",
                    "command": f"claude {_RELAXED}",
                    "kind": "claude_code",
                }
            ],
        }
    )
    backend = _CapturingBackend()
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        launch_backend=backend,
    )

    mgr.create(CreateWorkspaceRequest(agent_name="yolo", title="autonomous"))

    spec = backend.specs[0]
    assert spec.command == f"claude {_RELAXED}"
    assert _RELAXED not in " ".join(spec.decoration)


def test_no_engine_module_inspects_an_agents_permission_flags() -> None:
    """No code path filters agent argv — asserted structurally, not by convention.

    Comments and docstrings are excluded on purpose: this file and
    `container_policy` both *discuss* `--dangerously-skip-permissions` at length.
    What must not exist is a runtime string — a comparison, a scan, a rewrite.
    """
    offenders: list[str] = []
    # Anchored on the package, not the cwd, so the scan covers the same tree
    # regardless of where pytest was invoked from.
    package_root = Path(grove.__file__).parent
    for source in package_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in docstrings:
                continue
            if any(token in node.value for token in _GATE_TOKENS):
                offenders.append(f"{source}:{node.lineno}: {node.value!r}")

    assert offenders == []
