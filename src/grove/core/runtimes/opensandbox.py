"""OpenSandbox as a resident workspace runtime: one sandbox per workspace.

Driven over the server's lifecycle REST API, its documented surface. A
workspace is one sandbox created from the Grove runtime image whose entry
process is the native worker. The server has no stop that keeps a sandbox, so
freeze and stop both PAUSE it (on the Docker backend a ``docker pause``: memory
stays resident, and a host reboot loses it) and a ``grove.state`` metadata key
says which; delete is the only end. A thaw that finds the sandbox gone raises,
and a respawn from the pushed branch is the remedy.

Hibernation releases a frozen sandbox entirely: the server snapshots it
to a local image, ``docker`` pushes that to ``snapshot_registry`` and drops the
local copy, and only then is the sandbox deleted. Wake pulls it back and creates
a sandbox from the snapshot. The server's Docker backend keeps snapshots as
local images only, so this needs a ``docker`` client on the same engine.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final

import httpx
from loguru import logger

from grove.core.config import OpenSandboxRuntime
from grove.core.container_policy import EgressPolicy
from grove.core.errors import ContainerError
from grove.core.runtimes import (
    ProvisionRefused,
    RemoteHandle,
    RuntimeState,
    WorkspaceSpec,
)
from grove.core.runtimes._cli import ProviderCli
from grove.core.runtimes._env import RuntimeEnv
from grove.core.runtimes._http import response_detail

_API_KEY_HEADER: Final = "OPEN-SANDBOX-API-KEY"
_API_PREFIX: Final = "/v1"
# The server's lifecycle states. Pausing/Resuming are transitions, so they read
# as the state the sandbox is heading for only once it gets there.
_STATES: Final[dict[str, RuntimeState]] = {
    "Pending": RuntimeState.STARTING,
    "Resuming": RuntimeState.STARTING,
    "Running": RuntimeState.RUNNING,
    "Pausing": RuntimeState.RUNNING,
    "Stopping": RuntimeState.STOPPED,
    "Terminated": RuntimeState.STOPPED,
    "Failed": RuntimeState.STOPPED,
}
# Grove's own metadata telling a frozen sandbox from a stopped one: both are
# Paused to the server, and the sandbox is the only place that survives a
# daemon restart to remember which.
_STATE_KEY: Final = "grove.state"
_STOPPED: Final = "stopped"
_FROZEN: Final = "frozen"
# Quota exhausted (403) and pool capacity unavailable (429) are the server's
# own refusals; neither is retried.
_REFUSED: Final = frozenset({403, 429})
# A hibernated workload's ref: it names a stored snapshot, not a sandbox.
_SNAPSHOT_REF: Final = "snapshot:"
# The image the server's Docker backend commits a snapshot to, and restores from.
_LOCAL_SNAPSHOT_REPO: Final = "opensandbox-snapshots"
# Separates disposable restore images from the runtime image's own deployment tag.
_SNAPSHOT_TAG_PREFIX: Final = "snap-"
# What an OCI registry must answer a manifest read with for its digest.
_MANIFEST_TYPES: Final = (
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json"
)


class OpenSandboxDriver:
    """Drives one ``runtimes.<name>`` OpenSandbox server over its REST API."""

    def __init__(
        self,
        name: str,
        cfg: OpenSandboxRuntime,
        *,
        http: httpx.Client | None = None,
        registry: httpx.Client | None = None,
        cli: ProviderCli | None = None,
        sleep: Callable[[float], None] = time.sleep,
        resolve: Callable[[str], list[str]] | None = None,
    ) -> None:
        """Bind the driver to one configured server.

        Args:
            name: The ``runtimes.<name>`` key, stamped on every handle.
            cfg: That block's settings.
            http: The server client; built from ``cfg`` (base URL and API key
                from their ``*_env`` variables, timeout) when omitted.
            registry: The snapshot registry's client, for deleting a snapshot.
            cli: Runs ``docker`` for a snapshot's push and pull.
            sleep: Waits between snapshot readiness reads; tests pass a no-op.
            resolve: A hostname's IPv4 addresses for the egress allow list.
        """
        self._name = name
        self._cfg = cfg
        self._env = RuntimeEnv(name)
        self._http = http or httpx.Client(
            base_url=self._env.require("url_env", cfg.url_env).rstrip("/") + _API_PREFIX,
            headers={_API_KEY_HEADER: self._env.require("api_key_env", cfg.api_key_env)},
            timeout=cfg.http_timeout_seconds,
        )
        self._registry = registry or httpx.Client(timeout=cfg.http_timeout_seconds)
        self._cli = cli or ProviderCli(timeout_seconds=cfg.snapshot_timeout_seconds)
        self._sleep = sleep
        self._resolve = resolve or _ipv4

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """The ``POST /sandboxes`` body, without the worker's credential."""
        return json.dumps(self._request_body(spec, secrets=False), indent=2) + "\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Create the workspace's sandbox; its id is the handle.

        Raises:
            ProvisionRefused: The server's quota or pool capacity refused it.
        """
        return self.up_from(spec.workspace_id, self._request_body(spec, secrets=True))

    def up_from(self, workspace_id: str, body: dict[str, Any]) -> RemoteHandle:
        """``POST /sandboxes`` with a composed body; its id is the handle."""
        response = self._call("POST", "/sandboxes", json=body)
        if response.status_code in _REFUSED:
            raise ProvisionRefused(
                f"OpenSandbox refused {workspace_id}: {response_detail(response)}"
            )
        if response.status_code >= 400:
            raise ContainerError(
                f"failed to create a sandbox for {workspace_id}: {response_detail(response)}"
            )
        try:
            ref = str(response.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ContainerError(
                f"OpenSandbox answered without an id: {response_detail(response)}"
            ) from exc
        return RemoteHandle(runtime=self._name, ref=ref)

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Map the sandbox's state; ``None`` when the server cannot answer.

        A hibernated handle reads FROZEN while its snapshot exists.
        """
        if handle.ref.startswith(_SNAPSHOT_REF):
            snapshot = self._snapshot(_snapshot_id(handle))
            if snapshot is None:
                return None
            return RuntimeState.FROZEN if snapshot else RuntimeState.ABSENT
        found = self._read(handle)
        if found is None:
            return None
        if not found:
            return RuntimeState.ABSENT
        state = (found.get("status") or {}).get("state", "")
        if state == "Paused":
            stopped = (found.get("metadata") or {}).get(_STATE_KEY) == _STOPPED
            return RuntimeState.STOPPED if stopped else RuntimeState.FROZEN
        return _STATES.get(state)

    def stop(self, handle: RemoteHandle) -> None:
        """Pause the sandbox, marked stopped; the server has no stop that keeps one."""
        self._pause(handle, _STOPPED)

    def rm(self, handle: RemoteHandle) -> None:
        """Delete the sandbox; one that is already gone counts as removed."""
        response = self._call("DELETE", f"/sandboxes/{handle.ref}")
        if response.status_code not in (200, 202, 204, 404):
            raise ContainerError(
                f"failed to delete sandbox {handle.ref}: {response_detail(response)}"
            )

    def freeze(self, handle: RemoteHandle) -> None:
        """Pause the sandbox in place; the caller has pushed the branch first."""
        self._pause(handle, _FROZEN)

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Resume the same sandbox; its id is stable across pause and resume.

        Raises:
            ContainerError: The sandbox is gone (a server restart or an expiry);
                a respawn from the pushed branch is the remedy.
        """
        self._mutate("POST", f"/sandboxes/{handle.ref}/resume", handle, "resume")
        self._mutate(
            "PATCH", f"/sandboxes/{handle.ref}/metadata", handle, "mark", json={_STATE_KEY: ""}
        )
        return handle

    def hibernate(self, handle: RemoteHandle) -> RemoteHandle:
        """Snapshot the frozen sandbox, push it to the registry, then delete the sandbox.

        The server snapshots only a RUNNING sandbox (a paused one answers 409
        ``SNAPSHOT::INVALID_SOURCE_STATE``), so the sandbox is resumed for the
        snapshot, about two seconds with no prompt sent. The branch was pushed
        at freeze, so this does not push it again. Nothing is ended before the
        snapshot is in the registry. Any failed step, the sandbox delete included,
        drops the snapshot it made and pauses the sandbox again, so a frozen
        sandbox never has a snapshot and the next attempt starts from the same
        state this one did.

        Raises:
            ContainerError: The resume, the snapshot, the push or the delete failed.
        """
        self._mutate("POST", f"/sandboxes/{handle.ref}/resume", handle, "resume")
        try:
            sid = self._store_snapshot(handle)
        except ContainerError:
            self._pause(handle, _FROZEN)
            raise
        hibernated = RemoteHandle(runtime=self._name, ref=f"{_SNAPSHOT_REF}{sid}")
        try:
            self.rm(handle)
        except ContainerError:
            self._drop_snapshot(hibernated)
            self._pause(handle, _FROZEN)
            raise
        self._docker_rmi(self._remote_image(sid), self._local_image(sid))
        return hibernated

    def _drop_snapshot(self, hibernated: RemoteHandle) -> None:
        """Forget a snapshot the caller could not use; a failure only costs storage.

        Args:
            hibernated: The snapshot handle that was never handed back.
        """
        try:
            self.forget(hibernated)
        except ContainerError as exc:
            logger.warning("snapshot {} was not removed: {}", hibernated.ref, exc)

    def _store_snapshot(self, handle: RemoteHandle) -> str:
        """Snapshot a running sandbox and push the image to the snapshot registry.

        Args:
            handle: The sandbox, already running.

        Returns:
            The snapshot id, its image now in ``snapshot_registry``.

        Raises:
            ContainerError: A step failed; a snapshot the server made is dropped.
        """
        created = self._call("POST", f"/sandboxes/{handle.ref}/snapshots", json={})
        if created.status_code >= 400:
            raise ContainerError(f"could not snapshot {handle.ref}: {response_detail(created)}")
        try:
            sid = str(created.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ContainerError(
                f"OpenSandbox snapshot answered no id: {response_detail(created)}"
            ) from exc
        try:
            self._await_snapshot(sid)
            self._docker("tag", self._local_image(sid), self._remote_image(sid))
            with self._docker_auth() as auth:
                self._docker(*auth, "push", "-q", self._remote_image(sid))
        except ContainerError:
            self._call("DELETE", f"/snapshots/{sid}")
            raise
        return sid

    def wake(self, handle: RemoteHandle, spec: WorkspaceSpec) -> RemoteHandle:
        """Pull the snapshot back and create a sandbox from it with ``spec``'s launch.

        The snapshot stays intact until the restored worker registers with the
        daemon. A platform accepting a create proves only that it accepted one,
        not that the worker recovered its home and transcript.

        Raises:
            ContainerError: The pull or the create failed; the snapshot is kept.
        """
        sid = _snapshot_id(handle)
        with self._docker_auth() as auth:
            self._docker(*auth, "pull", "-q", self._remote_image(sid))
        self._docker("tag", self._remote_image(sid), self._local_image(sid))
        body = self._request_body(spec, secrets=True)
        # The snapshot names its own image; a runtime image here would restore nothing.
        del body["image"]
        body["snapshotId"] = sid
        return self.up_from(spec.workspace_id, body)

    def forget(self, handle: RemoteHandle) -> None:
        """Delete a hibernated handle's snapshot from the server and the registry.

        Raises:
            ContainerError: The registry refused the delete, so the snapshot is
                still stored and the record must keep naming it.
        """
        sid = _snapshot_id(handle)
        remote = self._remote_image(sid)
        self._call("DELETE", f"/snapshots/{sid}")
        self._docker_rmi(remote, self._local_image(sid))
        self._delete_manifest(remote)

    def _pause(self, handle: RemoteHandle, state: str) -> None:
        # Marked BEFORE the pause, so a sandbox never reads Paused under the
        # wrong name; a pause of an already-paused sandbox only re-marks it.
        self._mutate(
            "PATCH", f"/sandboxes/{handle.ref}/metadata", handle, "mark", json={_STATE_KEY: state}
        )
        found = self._read(handle) or {}
        if (found.get("status") or {}).get("state") != "Paused":
            self._mutate("POST", f"/sandboxes/{handle.ref}/pause", handle, "pause")

    def _local_image(self, sid: str) -> str:
        return f"{_LOCAL_SNAPSHOT_REPO}:{sid}"

    def _snapshot_repository(self) -> str:
        """The operator's snapshot repository, or the runtime image's own repository.

        Registries deduplicate blobs within one repository, not across package
        names. The runtime repository therefore avoids re-uploading its base
        layers, while an explicit ``snapshot_registry`` remains an override.
        """
        return self._cfg.snapshot_registry or _image_repository(self._cfg.image)

    def _remote_image(self, sid: str) -> str:
        return f"{self._snapshot_repository()}:{_SNAPSHOT_TAG_PREFIX}{sid}"

    def _snapshot(self, sid: str) -> dict[str, Any] | None:
        """The snapshot, ``{}`` when it does not exist, ``None`` when unreadable."""
        try:
            response = self._http.get(f"/snapshots/{sid}")
        except httpx.HTTPError:
            return None
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            return None
        try:
            found = response.json()
        except ValueError:
            return None
        return found if isinstance(found, dict) else None

    def _await_snapshot(self, sid: str) -> None:
        """Wait for the server to finish writing a snapshot, within the snapshot timeout."""
        deadline = time.monotonic() + self._cfg.snapshot_timeout_seconds
        while True:
            found = self._snapshot(sid) or {}
            state = (found.get("status") or {}).get("state", "")
            if state == "Ready":
                return
            if state == "Failed" or time.monotonic() > deadline:
                raise ContainerError(f"snapshot {sid} did not become ready (state {state!r})")
            self._sleep(self._cfg.snapshot_poll_seconds)

    def _docker(self, *argv: str) -> None:
        result = self._cli.call([self._cfg.docker_bin, *argv])
        if result is None:
            raise ContainerError(f"docker {argv[-2] if len(argv) > 1 else ''} could not run")
        if result.returncode != 0:
            raise ContainerError(f"docker {' '.join(argv[-2:])} failed: {result.detail}")

    def _docker_rmi(self, *images: str) -> None:
        # Best-effort: an image still present costs local disk, never correctness.
        result = self._cli.call([self._cfg.docker_bin, "rmi", *images])
        if result is None or result.returncode != 0:
            logger.warning("could not remove snapshot images {}", images)

    def _registry_credential(self) -> tuple[str, str]:
        """The snapshot registry's user and password from their ``*_env`` names, or blanks."""
        cfg = self._cfg
        user = self._env.optional("registry_user_env", cfg.registry_user_env)
        password = self._env.optional("registry_password_env", cfg.registry_password_env)
        return user, password

    @contextmanager
    def _docker_auth(self) -> Iterator[tuple[str, ...]]:
        """``--config <dir>`` holding only the registry credential, or nothing to add.

        A throwaway config directory keeps the user's own docker login untouched
        and the credential off every argv; it is removed as soon as the call ends.
        """
        user, password = self._registry_credential()
        if not user:
            yield ()
            return
        host = self._snapshot_repository().split("/", 1)[0]
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        root = tempfile.mkdtemp(prefix="grove-docker-")
        try:
            with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as config:
                json.dump({"auths": {host: {"auth": token}}}, config)
            yield ("--config", root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _delete_manifest(self, image: str) -> None:
        """Delete one snapshot tag's manifest through the OCI distribution API.

        Raises:
            ContainerError: ``image`` is not a snapshot tag, or the registry
                answered anything but success or absent.
        """
        repository, tag = _repository_and_tag(image)
        if not tag.startswith(_SNAPSHOT_TAG_PREFIX):
            raise ContainerError(f"refusing to delete non-snapshot tag {image!r}")
        host, _, path = repository.partition("/")
        base = f"https://{host}/v2/{path}/manifests"
        head = self._registry_request("HEAD", f"{base}/{tag}", path)
        if head.status_code == 404:
            return
        digest = head.headers.get("Docker-Content-Digest")
        if head.status_code >= 400 or not digest:
            raise ContainerError(f"registry could not read snapshot {tag}: HTTP {head.status_code}")
        gone = self._registry_request("DELETE", f"{base}/{digest}", path)
        if gone.status_code not in (200, 202, 204, 404):
            raise ContainerError(
                f"registry refused to delete snapshot {tag}: HTTP {gone.status_code}"
            )

    def _registry_request(self, method: str, url: str, repository: str) -> httpx.Response:
        """One registry call, answering a bearer challenge with the configured credential."""
        user, password = self._registry_credential()
        basic = (user, password) if user else None
        headers = {"Accept": _MANIFEST_TYPES}
        try:
            response = self._registry.request(method, url, headers=headers)
            challenge = response.headers.get("WWW-Authenticate", "")
            if response.status_code == 401 and challenge.lower().startswith("bearer"):
                params = dict(
                    part.split("=", 1)
                    for part in challenge[len("bearer ") :].split(",")
                    if "=" in part
                )
                realm = params.pop("realm", "").strip('"')
                query = {k: v.strip('"') for k, v in params.items()}
                query.setdefault("scope", f"repository:{repository}:pull,push,delete")
                token = self._registry.get(realm, params=query, auth=basic)
                bearer = token.json().get("token") or token.json().get("access_token", "")
                headers["Authorization"] = f"Bearer {bearer}"
                response = self._registry.request(method, url, headers=headers)
            elif response.status_code == 401 and basic is not None:
                response = self._registry.request(method, url, headers=headers, auth=basic)
        except (httpx.HTTPError, ValueError) as exc:
            raise ContainerError(f"snapshot registry did not answer: {exc}") from exc
        return response

    def _request_body(self, spec: WorkspaceSpec, *, secrets: bool) -> dict[str, Any]:
        cfg = self._cfg
        env = {**spec.env, **(spec.secret_env if secrets else {})}
        # Deny by default and allow only what the worker dials: the daemon and
        # the git remote it clones from, plus what the operator names (the
        # model API). Never left to the server's default.
        named = [EgressPolicy.remote_host(url) for url in spec.reach] + list(cfg.allow_hosts)
        targets = [allowed for name in named if name for allowed in self._two_layer(name)]
        image: dict[str, Any] = {"uri": spec.image}
        if secrets and cfg.image_auth_user_env:
            # A private registry's credential, like the worker bearer, travels
            # only in the request body and never in the emitted spec.
            image["auth"] = {
                "username": self._env.require("image_auth_user_env", cfg.image_auth_user_env),
                "password": self._env.require(
                    "image_auth_password_env", cfg.image_auth_password_env
                ),
            }
        body: dict[str, Any] = {
            "image": image,
            "entrypoint": list(spec.command),
            "env": env,
            "timeout": cfg.timeout_seconds,
            "networkPolicy": {
                "defaultAction": "deny",
                "egress": [
                    {"action": "allow", "target": target}
                    for target in dict.fromkeys(t for t in targets if t)
                ],
            },
            "metadata": {"grove.workspace": spec.workspace_id},
            "resourceLimits": dict(cfg.resources),
        }
        return body

    def _two_layer(self, target: str) -> list[str]:
        """A hostname plus the IPv4 addresses it resolves to now; an address as itself.

        A server enforcing egress in DNS and nftables lets a name resolve and
        then drops the connect unless the address is allowed too, so a
        name-only allow would block the worker's own clone. The address layer is
        wider than the name (one proxy fronts many names); the DNS layer still
        limits which names resolve. A name resolving to nothing contributes only
        itself and says so, and ``0.0.0.0`` is never an allow.
        """
        if _is_address(target):
            return [target]
        found = [a for a in self._resolve(target) if a != "0.0.0.0"]
        if not found:
            logger.warning("egress allow {} resolved to no IPv4 address; name-only allow", target)
        return [target, *found]

    def _mutate(
        self,
        method: str,
        path: str,
        handle: RemoteHandle,
        verb: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> None:
        response = self._call(method, path, json=json)
        if response.status_code >= 400:
            raise ContainerError(
                f"failed to {verb} sandbox {handle.ref}: {response_detail(response)}"
            )

    def _read(self, handle: RemoteHandle) -> dict[str, Any] | None:
        """The sandbox, ``{}`` when it does not exist, ``None`` when unreadable."""
        try:
            response = self._http.get(f"/sandboxes/{handle.ref}")
        except httpx.HTTPError:
            return None
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            return None
        try:
            found = response.json()
        except ValueError:
            return None
        return found if isinstance(found, dict) else None

    def _call(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ContainerError(f"OpenSandbox server did not answer: {exc}") from exc


def _image_repository(image: str) -> str:
    """The repository portion of an OCI image reference, without tag or digest."""
    reference = image.partition("@")[0]
    parent, separator, leaf = reference.rpartition("/")
    repository_leaf = leaf.rsplit(":", 1)[0]
    return f"{parent}{separator}{repository_leaf}"


def _repository_and_tag(image: str) -> tuple[str, str]:
    """The repository and tag portions of a tagged OCI image reference."""
    repository = _image_repository(image)
    tag = image.removeprefix(f"{repository}:")
    return repository, tag


def _ipv4(host: str) -> list[str]:
    """The IPv4 addresses ``host`` resolves to now, in resolver order; none on failure."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _is_address(target: str) -> bool:
    """Whether an allow target is a literal IP or CIDR rather than a name."""
    try:
        ipaddress.ip_network(target, strict=False)
    except ValueError:
        return False
    return True


def _snapshot_id(handle: RemoteHandle) -> str:
    if not handle.ref.startswith(_SNAPSHOT_REF):
        raise ContainerError(f"{handle.ref} is not a hibernated workload")
    return handle.ref.removeprefix(_SNAPSHOT_REF)


__all__ = ["OpenSandboxDriver"]
