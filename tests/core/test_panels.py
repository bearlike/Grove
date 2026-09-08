"""Panels resolve only inside the owning workspace's compose stack."""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.config import PanelConfig
from grove.core.container_runtime import ContainerRuntimeState, DockerCli
from grove.core.panels import PanelResolver
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

_FULL_ID = "a" * 64


class _Docker(DockerCli):
    """Script panel reads while retaining the production resolver's decisions."""

    def __init__(self, replies: dict[str, str]) -> None:
        super().__init__()
        self.commands: list[list[str]] = []
        self._replies = replies

    def read(self, argv: list[str]) -> str | None:
        self.commands.append(argv)
        joined = " ".join(argv)
        return next((reply for needle, reply in self._replies.items() if needle in joined), None)


def _state(**container_fields: object) -> WorkspaceState:
    container = ContainerRuntimeState.model_validate(
        {
            "container_id": _FULL_ID,
            "compose_project": "workspace-stack",
            "compose_owned": True,
            **container_fields,
        }
    )
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id="ws1",
        title="workspace",
        repo_root="/repo",
        branch="main",
        base_branch="main",
        worktree_path="/repo/.worktrees/ws1",
        tmux_session="grove-ws1",
        agent_name="claude",
        status=WorkspaceStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        runtime=Runtime.CONTAINER,
        container=container,
    )


def _panel() -> PanelConfig:
    return PanelConfig(name="desktop", title="Desktop", service="novnc", port=6080)


def test_resolves_exact_service_in_owned_compose_project() -> None:
    docker = _Docker({"docker ps": "service-container\n", "docker inspect": "172.22.0.4\n"})

    target = PanelResolver(docker=docker).resolve(_state(), _panel())

    assert target is not None
    assert (target.host, target.port) == ("172.22.0.4", 6080)
    command = docker.commands[0]
    assert "label=com.docker.compose.project=workspace-stack" in command
    assert "label=com.docker.compose.service=novnc" in command


def test_unowned_or_nonlive_workspace_never_queries_docker() -> None:
    docker = _Docker({"docker ps": "foreign\n"})
    resolver = PanelResolver(docker=docker)

    assert resolver.resolve(_state(compose_owned=False), _panel()) is None
    paused = _state()
    paused.status = WorkspaceStatus.PAUSED
    assert resolver.resolve(paused, _panel()) is None
    assert docker.commands == []


def test_scaled_service_or_non_ipv4_answer_is_unavailable() -> None:
    scaled = PanelResolver(
        docker=_Docker({"docker ps": "one\ntwo\n", "docker inspect": "172.22.0.4\n"})
    )
    ipv6 = PanelResolver(docker=_Docker({"docker ps": "one\n", "docker inspect": "::1\n"}))

    assert scaled.resolve(_state(), _panel()) is None
    assert ipv6.resolve(_state(), _panel()) is None
