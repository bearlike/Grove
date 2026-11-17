"""OpenFaaS as a resident workspace runtime: one single-replica function per workspace.

Driven through the gateway's REST API, the provider's own surface. A workspace
is a function named ``grove-<id>``, pinned to one replica. Freeze scales it to
zero and thaw back to one, so unlike the job platforms thaw keeps the handle;
the container still restarts cold and resumes from the pushed branch.
Scale-to-zero is an OpenFaaS Pro feature.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

import httpx

from grove.core.config import OpenFaasRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import (
    RESUME_ENV,
    ProvisionRefused,
    RemoteHandle,
    RuntimeState,
    WorkspaceSpec,
)
from grove.core.runtimes._env import RuntimeEnv
from grove.core.runtimes._http import response_detail

# The labels OpenFaaS reads a function's replica bounds from.
_SCALE_MIN: Final = "com.openfaas.scale.min"
_SCALE_MAX: Final = "com.openfaas.scale.max"
# Grove's own label telling a frozen function from a stopped one: both are zero
# replicas to the platform, and the function is the only place that survives a
# daemon restart to remember which.
_STATE_LABEL: Final = "grove.state"
_FROZEN: Final = "frozen"
_STOPPED: Final = "stopped"
_NAME_PREFIX: Final = "grove-"


class OpenFaasDriver:
    """Drives one ``runtimes.<name>`` OpenFaaS gateway over its REST API."""

    def __init__(
        self, name: str, cfg: OpenFaasRuntime, *, http: httpx.Client | None = None
    ) -> None:
        """Bind the driver to one configured gateway.

        Args:
            name: The ``runtimes.<name>`` key, stamped on every handle.
            cfg: That block's settings.
            http: The gateway client; built from ``cfg`` (base URL, basic auth
                from ``password_env``, timeout) when omitted.
        """
        self._name = name
        self._cfg = cfg
        self._http = http or httpx.Client(
            base_url=cfg.gateway_url,
            auth=(cfg.username, RuntimeEnv(name).require("password_env", cfg.password_env)),
            timeout=cfg.http_timeout_seconds,
        )

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """A ``stack.yml`` with this workspace's function, for ``faas-cli deploy``.

        JSON, which every YAML reader accepts.
        """
        function = self._function(spec, spec.env, scale=1)
        stack = {
            "version": 1.0,
            "provider": {"name": "openfaas", "gateway": self._cfg.gateway_url},
            "functions": {
                function["service"]: {
                    "image": function["image"],
                    "namespace": function["namespace"],
                    "environment": function["envVars"],
                    "labels": function["labels"],
                    "secrets": function["secrets"],
                }
            },
        }
        return json.dumps(stack, indent=2) + "\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Deploy (or redeploy) the workspace's function at one replica."""
        function = self._function(spec, spec.env, scale=1)
        self._deploy(function)
        return RemoteHandle(runtime=self._name, ref=function["service"])

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Map replica counts onto a state; ``None`` when the gateway cannot answer."""
        found = self._read(handle)
        if found is None:
            return None
        if not found:
            return RuntimeState.ABSENT
        if found.get("availableReplicas", 0) >= 1:
            return RuntimeState.RUNNING
        if found.get("replicas", 0) == 0:
            labels = found.get("labels") or {}
            return (
                RuntimeState.FROZEN if labels.get(_STATE_LABEL) == _FROZEN else RuntimeState.STOPPED
            )
        return RuntimeState.STARTING

    def stop(self, handle: RemoteHandle) -> None:
        """Scale to zero; the function and its configuration stay for a restart."""
        self._rescale(handle, 0, _STOPPED)

    def rm(self, handle: RemoteHandle) -> None:
        """Delete the function; one that is already gone counts as removed."""
        response = self._request(
            "DELETE",
            "/system/functions",
            json={"functionName": handle.ref, "namespace": self._cfg.namespace},
        )
        if response.status_code not in (200, 202, 404):
            raise ContainerError(f"failed to remove {handle.ref}: {response_detail(response)}")

    def freeze(self, handle: RemoteHandle) -> None:
        """Scale to zero (OpenFaaS Pro); the caller has pushed the branch first."""
        self._rescale(handle, 0, _FROZEN)

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Scale back to one replica, marked as a resume; the handle is unchanged."""
        self._rescale(handle, 1, "", resume=True)
        return handle

    def _rescale(
        self, handle: RemoteHandle, replicas: int, state: str, *, resume: bool = False
    ) -> None:
        """Redeploy the live function with new replica bounds and state label, then scale it.

        The redeploy is what records the bounds and label on the function; the
        scale call moves the running replicas now rather than at the next sync.
        """
        found = self._read(handle)
        if not found:
            raise ContainerError(f"cannot scale {handle.ref}: the function cannot be read")
        env = dict(found.get("envVars") or {})
        if resume:
            env[RESUME_ENV] = "1"
        labels = {
            **(found.get("labels") or {}),
            _SCALE_MIN: str(replicas),
            _SCALE_MAX: str(replicas),
        }
        labels[_STATE_LABEL] = state
        self._deploy(
            {
                "service": found["name"],
                "image": found["image"],
                "namespace": found.get("namespace", self._cfg.namespace),
                "envProcess": found.get("envProcess", ""),
                "secrets": found.get("secrets") or [self._cfg.daemon_token_secret],
                "envVars": env,
                "labels": labels,
            }
        )
        self._scale(handle, replicas)

    def _function(
        self, spec: WorkspaceSpec, env: Mapping[str, str], *, scale: int
    ) -> dict[str, Any]:
        cfg = self._cfg
        return {
            "service": f"{_NAME_PREFIX}{spec.workspace_id}",
            "image": spec.image,
            "namespace": cfg.namespace,
            "envProcess": " ".join(spec.command),
            "envVars": {
                **dict(env),
                "exec_timeout": cfg.exec_timeout,
                "read_timeout": cfg.exec_timeout,
                "write_timeout": cfg.exec_timeout,
            },
            "labels": {_SCALE_MIN: str(scale), _SCALE_MAX: str(scale)},
            "secrets": [cfg.daemon_token_secret],
        }

    def _deploy(self, function: dict[str, Any]) -> None:
        response = self._request("PUT", "/system/functions", json=function)
        if response.status_code == 404:
            response = self._request("POST", "/system/functions", json=function)
        if response.status_code >= 500:
            raise ProvisionRefused(
                f"OpenFaaS refused {function['service']}: {response_detail(response)}"
            )
        if response.status_code >= 400:
            raise ContainerError(
                f"failed to deploy {function['service']}: {response_detail(response)}"
            )

    def _scale(self, handle: RemoteHandle, replicas: int) -> None:
        response = self._request(
            "POST",
            f"/system/scale-function/{handle.ref}",
            json={
                "serviceName": handle.ref,
                "namespace": self._cfg.namespace,
                "replicas": replicas,
            },
        )
        if response.status_code >= 400:
            raise ContainerError(
                f"failed to scale {handle.ref} to {replicas}: {response_detail(response)}"
            )

    def _read(self, handle: RemoteHandle) -> dict[str, Any] | None:
        """The function status, ``{}`` when it does not exist, ``None`` when unreadable."""
        try:
            response = self._http.get(
                f"/system/function/{handle.ref}", params={"namespace": self._cfg.namespace}
            )
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

    def _request(self, method: str, path: str, *, json: dict[str, Any]) -> httpx.Response:
        try:
            return self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ContainerError(
                f"OpenFaaS gateway {self._cfg.gateway_url} did not answer: {exc}"
            ) from exc


__all__ = ["OpenFaasDriver"]
