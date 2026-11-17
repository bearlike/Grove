"""The OpenSandbox driver against an in-memory lifecycle server behind ``httpx.MockTransport``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import count
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from grove.core.config import OpenSandboxRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused, RemoteHandle, RuntimeState, WorkspaceSpec
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.opensandbox import OpenSandboxDriver
from tests.core.test_runtimes_contract import (
    _SPEC,
    assert_driver_conformance,
    assert_hibernate_conformance,
)

_CFG = OpenSandboxRuntime(
    image="registry.example.com/grove-runtime:1",
    allow_hosts=["api.example.com", "203.0.113.0/24"],
    resources={"cpu": "2", "memory": "4Gi"},
    image_auth_user_env="SBX_REGISTRY_USER",
    image_auth_password_env="SBX_REGISTRY_PASSWORD",
    snapshot_registry="registry.example.com/org/grove-snapshots",
    registry_user_env="SBX_SNAPSHOT_USER",
    registry_password_env="SBX_SNAPSHOT_TOKEN",
)
_WORKER = WorkspaceSpec(
    workspace_id="a" * 32,
    image="registry.example.com/grove-runtime:1",
    command=("grove-native-worker",),
    env={"GROVE_WORKER_CONFIG": "{}"},
    secret_env={"GROVE_WORKER_TOKEN": "s3cret-bearer"},
    reach=("https://grove.example.test", "git@git.example.test:acme/app.git"),
)


class FakeServer:
    """The ``/sandboxes`` routes the driver calls, holding sandboxes in memory."""

    def __init__(self, *, refuse: int | None = None) -> None:
        self.refuse = refuse
        self.sandboxes: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self._ids = count(1)

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 — one return per route
        self.requests.append(request)
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "k"
        parts = request.url.path.strip("/").split("/")
        body = json.loads(request.content) if request.content else {}
        if parts[0] == "snapshots":
            return self._snapshot(request.method, parts[1])
        if parts == ["sandboxes"]:
            if self.refuse:
                return httpx.Response(
                    self.refuse, json={"code": "KUBERNETES::QUOTA_EXCEEDED", "message": "quota"}
                )
            ref = f"sbx-{next(self._ids)}"
            self.sandboxes[ref] = {
                "id": ref,
                "status": {"state": "Running"},
                "metadata": body["metadata"],
                "request": body,
            }
            return httpx.Response(202, json={"id": ref, "status": {"state": "Pending"}})
        found = self.sandboxes.get(parts[1])
        if found is None:
            return httpx.Response(404, json={"code": "NOT_FOUND", "message": "no such sandbox"})
        verb = parts[2] if len(parts) > 2 else None
        if request.method == "GET":
            return httpx.Response(200, json=found)
        if request.method == "DELETE":
            del self.sandboxes[parts[1]]
            return httpx.Response(204)
        if verb == "snapshots":
            # The live server (release-1.1.1, Docker backend) refuses a paused source.
            if found["status"]["state"] != "Running":
                return httpx.Response(
                    409,
                    json={
                        "code": "SNAPSHOT::INVALID_SOURCE_STATE",
                        "message": "Snapshot can only be created from a Running sandbox.",
                    },
                )
            sid = f"snap-{next(self._ids)}"
            self.snapshots[sid] = {"id": sid, "status": {"state": "Ready"}}
            return httpx.Response(202, json={"id": sid, "status": {"state": "Creating"}})
        if verb == "metadata":
            found["metadata"] = {**found["metadata"], **body}
        elif verb in ("pause", "resume"):
            found["status"] = {"state": "Paused" if verb == "pause" else "Running"}
        return httpx.Response(202)

    def _snapshot(self, method: str, sid: str) -> httpx.Response:
        if method == "DELETE":
            self.snapshots.pop(sid, None)
            return httpx.Response(204)
        snapshot = self.snapshots.get(sid)
        return httpx.Response(200, json=snapshot) if snapshot else httpx.Response(404)


class FakeDocker(ProviderCli):
    """Records every ``docker`` argv and keeps the images it was told about."""

    def __init__(self, *, refuse: str = "") -> None:
        super().__init__(timeout_seconds=1)
        self.refuse = refuse
        self.calls: list[list[str]] = []
        self.images: set[str] = set()
        self.pushed: set[str] = set()

    def call(self, argv: Sequence[str]) -> CliResult | None:
        argv = list(argv)
        self.calls.append(argv)
        words = [a for a in argv[1:] if a not in ("-q",)]
        if words[0] == "--config":
            words = words[2:]
        verb, *rest = words
        if verb == self.refuse:
            return CliResult(1, "", f"{verb} denied")
        if verb == "tag":
            self.images.add(rest[1])
        elif verb == "push":
            self.pushed.add(rest[0])
        elif verb == "pull":
            self.images.add(rest[0])
        elif verb == "rmi":
            self.images.difference_update(rest)
        return CliResult(0, "", "")


class FakeRegistry:
    """An OCI registry answering a manifest HEAD and a delete by digest."""

    def __init__(self, docker: FakeDocker) -> None:
        self.docker = docker
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        tag = request.url.path.rsplit("/", 1)[-1]
        if request.method == "HEAD":
            held = any(image.endswith(f":{tag}") for image in self.docker.pushed)
            if not held:
                return httpx.Response(404)
            return httpx.Response(200, headers={"Docker-Content-Digest": f"sha256:{tag}"})
        self.deleted.append(tag)
        self.docker.pushed = {i for i in self.docker.pushed if not i.endswith(f":{tag[7:]}")}
        return httpx.Response(202)


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every variable ``_CFG`` names: an unset one is refused by name (#1049).

    A test about the refusal deletes the variable it means to leave unset.
    """
    for variable, value in (
        ("SBX_REGISTRY_USER", "robot"),
        ("SBX_REGISTRY_PASSWORD", "image-pw"),
        ("SBX_SNAPSHOT_USER", "robot"),
        ("SBX_SNAPSHOT_TOKEN", "snapshot-pw"),
    ):
        monkeypatch.setenv(variable, value)


def _driver(
    server: FakeServer, docker: FakeDocker | None = None, cfg: OpenSandboxRuntime = _CFG
) -> OpenSandboxDriver:
    client = httpx.Client(
        base_url="https://sandbox.example.test",
        headers={"OPEN-SANDBOX-API-KEY": "k"},
        transport=httpx.MockTransport(server),
    )
    docker = docker or FakeDocker()
    registry = httpx.Client(transport=httpx.MockTransport(FakeRegistry(docker)))
    return OpenSandboxDriver(
        "sbx",
        cfg,
        http=client,
        registry=registry,
        cli=docker,
        sleep=lambda _: None,
        resolve=_RESOLVE.get,
    )


# A fixed resolver so no test reaches real DNS: one name resolves twice, one to
# nothing usable, and the others not at all.
_ADDRESSES = {"grove.example.test": ["192.0.2.10"], "git.example.test": ["192.0.2.20", "0.0.0.0"]}


class _Resolve:
    def get(self, host: str) -> list[str]:
        return _ADDRESSES.get(host, [])


_RESOLVE = _Resolve()


def test_opensandbox_driver_conforms() -> None:
    assert_driver_conformance(_driver(FakeServer()))


@pytest.mark.parametrize("code", [403, 429])
def test_a_quota_or_capacity_refusal_is_provision_refused_and_not_retried(code: int) -> None:
    server = FakeServer(refuse=code)
    with pytest.raises(ProvisionRefused, match="quota"):
        _driver(server).up(_SPEC)
    assert len(server.requests) == 1


def test_the_create_request_denies_egress_but_the_daemon_and_the_git_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBX_REGISTRY_USER", "robot")
    monkeypatch.setenv("SBX_REGISTRY_PASSWORD", "registry-pw")
    server = FakeServer()
    _driver(server).up(_WORKER)
    body = json.loads(server.requests[0].content)
    assert body["image"] == {
        "uri": _WORKER.image,
        "auth": {"username": "robot", "password": "registry-pw"},
    }
    assert body["entrypoint"] == ["grove-native-worker"]
    assert body["networkPolicy"] == {
        "defaultAction": "deny",
        "egress": [
            {"action": "allow", "target": "grove.example.test"},
            {"action": "allow", "target": "192.0.2.10"},
            {"action": "allow", "target": "git.example.test"},
            {"action": "allow", "target": "192.0.2.20"},
            {"action": "allow", "target": "api.example.com"},
            {"action": "allow", "target": "203.0.113.0/24"},
        ],
    }
    assert body["env"]["GROVE_WORKER_TOKEN"] == "s3cret-bearer"


def test_the_emitted_spec_never_carries_the_worker_bearer_or_registry_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBX_REGISTRY_USER", "robot")
    monkeypatch.setenv("SBX_REGISTRY_PASSWORD", "registry-pw")
    emitted = _driver(FakeServer()).emit_spec(_WORKER)
    assert "s3cret-bearer" not in emitted and "registry-pw" not in emitted
    assert "GROVE_WORKER_CONFIG" in emitted


def test_freeze_and_stop_both_pause_but_read_differently() -> None:
    driver = _driver(FakeServer())
    handle = driver.up(_SPEC)
    driver.freeze(handle)
    assert driver.status(handle) is RuntimeState.FROZEN
    driver.stop(handle)
    assert driver.status(handle) is RuntimeState.STOPPED
    assert driver.thaw(handle) == handle
    assert driver.status(handle) is RuntimeState.RUNNING


def test_thawing_a_vanished_sandbox_raises_rather_than_starting_a_new_one() -> None:
    driver = _driver(FakeServer())
    with pytest.raises(ContainerError, match="no such sandbox"):
        driver.thaw(RemoteHandle("sbx", "gone"))


def test_an_unreachable_server_reads_none_not_absent() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(
        base_url="https://sandbox.example.test", transport=httpx.MockTransport(down)
    )
    assert OpenSandboxDriver("sbx", _CFG, http=client).status(RemoteHandle("sbx", "x")) is None


def test_opensandbox_hibernates_and_wakes_conformantly() -> None:
    assert_hibernate_conformance(_driver(FakeServer()))


def test_hibernate_pushes_the_snapshot_before_it_deletes_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBX_SNAPSHOT_USER", "robot")
    monkeypatch.setenv("SBX_SNAPSHOT_TOKEN", "snapshot-pw")
    server, docker = FakeServer(), FakeDocker()
    driver = _driver(server, docker)
    handle = driver.up(_WORKER)
    driver.freeze(handle)

    hibernated = driver.hibernate(handle)

    sid = hibernated.ref.removeprefix("snapshot:")
    assert hibernated == RemoteHandle("sbx", f"snapshot:{sid}")
    remote = f"registry.example.com/org/grove-snapshots:snap-{sid}"
    assert docker.pushed == {remote}
    assert docker.images == set(), "both local tags are dropped after the push"
    assert handle.ref not in server.sandboxes
    pushes = [c for c in docker.calls if "push" in c]
    assert pushes[0][1] == "--config", "credentials ride a throwaway config, never argv"
    assert all("snapshot-pw" not in arg for call in docker.calls for arg in call)


def test_a_failed_push_leaves_the_sandbox_paused_and_drops_the_half_snapshot() -> None:
    server = FakeServer()
    driver = _driver(server, FakeDocker(refuse="push"))
    handle = driver.up(_WORKER)
    driver.freeze(handle)
    with pytest.raises(ContainerError, match="push"):
        driver.hibernate(handle)
    assert driver.status(handle) is RuntimeState.FROZEN
    # Paused again, never left running.
    assert server.sandboxes[handle.ref]["status"]["state"] == "Paused"
    assert server.snapshots == {}


def test_hibernate_resumes_the_frozen_sandbox_for_the_snapshot_and_pushes_no_branch() -> None:
    """The live server refused a paused source (409, 2026-10-22), so the resume
    is what lets the snapshot happen at all. The branch was pushed at freeze, so
    the worker is never asked to push again in the window it is running."""
    server = FakeServer()
    driver = _driver(server, FakeDocker())
    handle = driver.up(_WORKER)
    driver.freeze(handle)
    del server.requests[:]

    driver.hibernate(handle)

    calls = [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in server.requests]
    assert calls.index(("POST", "resume")) < calls.index(("POST", "snapshots"))
    assert not any("push" in str(r.content) for r in server.requests)


def test_a_refused_snapshot_pauses_the_resumed_sandbox_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeServer()
    driver = _driver(server, FakeDocker())
    handle = driver.up(_WORKER)
    driver.freeze(handle)
    real = server.__call__

    def refuse_snapshots(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/snapshots") and request.method == "POST":
            server.requests.append(request)
            return httpx.Response(507, json={"message": "disk full"})
        return real(request)

    driver._http._transport = httpx.MockTransport(refuse_snapshots)  # type: ignore[attr-defined]
    with pytest.raises(ContainerError, match="disk full"):
        driver.hibernate(handle)
    assert server.sandboxes[handle.ref]["status"]["state"] == "Paused"
    assert driver.status(handle) is RuntimeState.FROZEN


def test_a_failed_delete_after_the_push_pauses_again_and_keeps_no_snapshot() -> None:
    """The delete comes after the snapshot is in the registry. Its failure must
    still leave the sandbox frozen and snapshot-free: a running one would be
    re-snapshotted by every idle retry with a worker nobody steered."""
    server, docker = FakeServer(), FakeDocker()
    driver = _driver(server, docker)
    handle = driver.up(_WORKER)
    driver.freeze(handle)
    real = server.__call__

    def refuse_delete_once(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE" and request.url.path.endswith(handle.ref):
            driver._http._transport = httpx.MockTransport(real)  # type: ignore[attr-defined]
            return httpx.Response(500, json={"message": "engine busy"})
        return real(request)

    driver._http._transport = httpx.MockTransport(refuse_delete_once)  # type: ignore[attr-defined]
    with pytest.raises(ContainerError, match="engine busy"):
        driver.hibernate(handle)
    assert server.sandboxes[handle.ref]["status"]["state"] == "Paused"
    assert driver.status(handle) is RuntimeState.FROZEN
    assert server.snapshots == {}
    assert docker.pushed == set(), "the pushed snapshot is dropped from the registry too"


def test_wake_restores_from_the_snapshot_without_forgetting_it() -> None:
    server, docker = FakeServer(), FakeDocker()
    driver = _driver(server, docker)
    handle = driver.up(_WORKER)
    driver.freeze(handle)
    hibernated = driver.hibernate(handle)
    sid = hibernated.ref.removeprefix("snapshot:")

    woken = driver.wake(hibernated, _WORKER)

    request = server.sandboxes[woken.ref]["request"]
    assert request["snapshotId"] == sid and "image" not in request
    assert request["env"]["GROVE_WORKER_TOKEN"] == "s3cret-bearer"
    assert docker.pushed and sid in server.snapshots


def test_hibernate_defaults_to_a_snapshot_tag_on_the_runtime_image_repository() -> None:
    server, docker = FakeServer(), FakeDocker()
    driver = _driver(server, docker, _CFG.model_copy(update={"snapshot_registry": ""}))
    handle = driver.up(_WORKER)
    driver.freeze(handle)

    hibernated = driver.hibernate(handle)

    sid = hibernated.ref.removeprefix("snapshot:")
    assert docker.pushed == {f"registry.example.com/grove-runtime:snap-{sid}"}


def test_forget_refuses_to_delete_a_runtime_image_tag() -> None:
    driver = _driver(FakeServer())

    with pytest.raises(ContainerError, match="snapshot tag"):
        driver._delete_manifest("registry.example.com/grove-runtime:1")


def test_a_validation_error_that_echoes_the_request_never_reaches_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBX_REGISTRY_USER", "robot")
    monkeypatch.setenv("SBX_REGISTRY_PASSWORD", "registry-pw")

    def echoing(request: httpx.Request) -> httpx.Response:
        echoed = json.loads(request.content)
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "value_error",
                        "loc": ["body"],
                        "msg": "resourceLimits is required",
                        "input": echoed,
                    }
                ]
            },
        )

    client = httpx.Client(
        base_url="https://sandbox.example.test", transport=httpx.MockTransport(echoing)
    )
    with pytest.raises(ContainerError) as refused:
        OpenSandboxDriver("sbx", _CFG, http=client).up(_WORKER)
    message = str(refused.value)
    assert "resourceLimits is required" in message
    assert "s3cret-bearer" not in message and "registry-pw" not in message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"code": "QUOTA", "message": "quota"}, "QUOTA: quota"),
        ({"detail": "plain words"}, "plain words"),
        ({"input": {"token": "leak"}}, "422 Unprocessable Entity"),
        (None, "422 Unprocessable Entity"),
    ],
)
def test_response_detail_reads_only_human_fields(body: object, expected: str) -> None:
    from grove.core.runtimes._http import response_detail  # noqa: PLC0415

    response = (
        httpx.Response(422, json=body)
        if body is not None
        else httpx.Response(422, text="raw leak body")
    )
    assert response_detail(response) == expected


def test_a_block_without_resources_is_refused_at_config_load() -> None:
    with pytest.raises(ValidationError, match="resources"):
        OpenSandboxRuntime.model_validate({"image": "registry.example.com/grove-runtime:1"})
    with pytest.raises(ValidationError, match="resources"):
        OpenSandboxRuntime.model_validate({"image": "i", "resources": {}})


def test_every_create_carries_the_resource_limits() -> None:
    server = FakeServer()
    _driver(server).up(_WORKER)
    assert json.loads(server.requests[0].content)["resourceLimits"] == {"cpu": "2", "memory": "4Gi"}
