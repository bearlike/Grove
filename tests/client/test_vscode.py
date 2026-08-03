"""VS Code attach: URI builder, mount contribution, launch."""

from __future__ import annotations

import binascii
import json
from dataclasses import dataclass

import pytest

from grove.client.errors import TransportError
from grove.client.vscode import (
    VolumeMount,
    VsCodeAttach,
    build_vscode_uri,
    vscode_server_mount,
)


def test_build_vscode_uri_matches_known_hex() -> None:
    # Hand-computed: json.dumps({"containerName": "/grove-abc123"}, separators=(",", ":"))
    # hex-encoded, per the issue's own worked example.
    uri = build_vscode_uri("grove-abc123", "/workspace")
    expected_authority = (
        "attached-container+7b22636f6e7461696e65724e616d65223a222f67726f76652d616263313233227d"
    )
    assert uri == f"vscode-remote://{expected_authority}/workspace"


def test_build_vscode_uri_carries_non_default_context() -> None:
    uri = build_vscode_uri("grove-abc123", "/workspace", context="remote-host")
    # settings.context is a key alongside containerName in the same JSON blob —
    # decode the authority back to prove the shape rather than re-deriving hex.
    authority = uri.removeprefix("vscode-remote://").split("/", 1)[0]
    hex_part = authority.removeprefix("attached-container+")
    payload = json.loads(binascii.unhexlify(hex_part))
    assert payload == {"containerName": "/grove-abc123", "settings": {"context": "remote-host"}}


def test_build_vscode_uri_no_context_omits_settings() -> None:
    uri = build_vscode_uri("grove-abc123", "/workspace")
    assert "settings" not in uri  # no stray key when context is unset


def test_vscode_server_mount_default_home() -> None:
    mount = vscode_server_mount("grove-vscode-server-myproject")
    assert mount == VolumeMount(
        source="grove-vscode-server-myproject", target="/root/.vscode-server"
    )
    assert mount.to_flag() == (
        "type=volume,source=grove-vscode-server-myproject,target=/root/.vscode-server"
    )


def test_vscode_server_mount_custom_home() -> None:
    mount = vscode_server_mount("vol", remote_home="/home/vscode")
    assert mount.target == "/home/vscode/.vscode-server"


@dataclass(slots=True)
class _FakeContainer:
    container_id: str
    remote_workspace_folder: str


def test_vscode_attach_builds_uri_from_container_target() -> None:
    target = _FakeContainer(container_id="grove-abc123", remote_workspace_folder="/workspace")
    attach = VsCodeAttach(target)
    assert attach.uri == build_vscode_uri("grove-abc123", "/workspace")


async def test_vscode_attach_launches_resolved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "code" else None

    def fake_popen(argv: list[str], **kwargs: object) -> None:
        calls.append(argv)
        assert kwargs.get("start_new_session") is True

    monkeypatch.setattr("grove.client.vscode.shutil.which", fake_which)
    monkeypatch.setattr("grove.client.vscode.subprocess.Popen", fake_popen)

    target = _FakeContainer(container_id="grove-abc123", remote_workspace_folder="/workspace")
    attach = VsCodeAttach(target)
    await attach.start()

    assert calls == [["/usr/bin/code", "--folder-uri", attach.uri]]


async def test_vscode_attach_falls_back_to_insiders(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/code-insiders" if name == "code-insiders" else None

    calls: list[list[str]] = []
    monkeypatch.setattr("grove.client.vscode.shutil.which", fake_which)
    monkeypatch.setattr(
        "grove.client.vscode.subprocess.Popen", lambda argv, **kwargs: calls.append(argv)
    )

    attach = VsCodeAttach(_FakeContainer(container_id="c", remote_workspace_folder="/w"))
    await attach.start()

    assert calls == [["/usr/bin/code-insiders", "--folder-uri", attach.uri]]


async def test_vscode_attach_raises_actionable_error_when_code_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grove.client.vscode.shutil.which", lambda name: None)

    attach = VsCodeAttach(_FakeContainer(container_id="c", remote_workspace_folder="/w"))
    with pytest.raises(TransportError, match="not found on PATH"):
        await attach.start()
