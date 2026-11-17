"""Azure Container Apps jobs as a resident workspace runtime: one execution per workspace.

The job is the customer's (they apply the emitted template with ``az containerapp
job create --yaml``); a workspace is one manual-trigger EXECUTION of it. A start
override replaces the WHOLE template for that execution, so ``up`` sends the full
container, never a delta. ACA has no suspend and keeps executions only as
history, so ``freeze``, ``stop`` and ``rm`` all end the execution, and ``thaw``
starts a fresh one from the pushed branch.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping, Sequence
from typing import Any, Final

from grove.core.config import AcaJobRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import (
    RESUME_ENV,
    WORKER_TOKEN_ENV,
    ProvisionRefused,
    RemoteHandle,
    RuntimeState,
    WorkspaceSpec,
)
from grove.core.runtimes._cli import CliResult, ProviderCli

# az's own words for "no such execution" and for a quota or capacity refusal.
_NOT_FOUND: Final = ("ResourceNotFound", "NotFound", "was not found")
_REFUSED: Final = ("QuotaExceeded", "exceeds", "quota", "InsufficientCapacity")
# `properties.status` of an execution, onto the contract's states.
_STATES: Final[Mapping[str, RuntimeState]] = {
    "Pending": RuntimeState.STARTING,
    "Processing": RuntimeState.STARTING,
    "Running": RuntimeState.RUNNING,
    "Succeeded": RuntimeState.STOPPED,
    "Failed": RuntimeState.STOPPED,
    "Stopped": RuntimeState.STOPPED,
    "Degraded": RuntimeState.STOPPED,
}
# The variable the token secret is mounted under inside the job.
_TOKEN_SECRET_REF: Final = "daemon-token"
_CONTAINER_NAME: Final = "worker"


class AcaJobDriver:
    """Drives one ``runtimes.<name>`` Container Apps job through ``az``."""

    def __init__(self, name: str, cfg: AcaJobRuntime, *, cli: ProviderCli | None = None) -> None:
        """Bind the driver to one configured job.

        Args:
            name: The ``runtimes.<name>`` key, stamped on every handle.
            cfg: That block's settings.
            cli: The ``az`` process edge; built from ``cfg`` when omitted.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(timeout_seconds=cfg.cli_timeout_seconds, env=os.environ)

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """A manual-trigger job template for ``az containerapp job create --yaml``.

        JSON, which every YAML reader accepts. The daemon token is a job secret
        whose value the operator sets; the template only references it by name.
        """
        cfg = self._cfg
        container: dict[str, Any] = {
            "name": _CONTAINER_NAME,
            "image": spec.image,
            "command": list(spec.command),
            "env": [
                *({"name": k, "value": v} for k, v in spec.env.items()),
                {"name": WORKER_TOKEN_ENV, "secretRef": _TOKEN_SECRET_REF},
            ],
            "resources": {"cpu": cfg.cpu, "memory": cfg.memory},
        }
        template: dict[str, Any] = {"containers": [container]}
        if cfg.storage_name:
            template["volumes"] = [
                {"name": "work", "storageType": "AzureFile", "storageName": cfg.storage_name}
            ]
            container["volumeMounts"] = [{"volumeName": "work", "mountPath": "/home/grove/work"}]
        job = {
            "properties": {
                "environmentId": cfg.environment,
                "configuration": {
                    "triggerType": "Manual",
                    "replicaTimeout": cfg.replica_timeout_seconds,
                    "replicaRetryLimit": 0,
                    "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1},
                    "secrets": [{"name": _TOKEN_SECRET_REF}],
                },
                "template": template,
            },
        }
        return json.dumps(job, indent=2) + "\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Start one execution carrying the full container for this workspace."""
        return self._start(spec.image, spec.command, spec.env)

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Read the execution by NAME (history keeps only the latest 100, so never by position)."""
        found = self._show(handle)
        if found is None:
            return None
        if not found:
            return RuntimeState.ABSENT
        return _STATES.get(found.get("properties", {}).get("status", ""))

    def stop(self, handle: RemoteHandle) -> None:
        """Stop the execution."""
        result = self._cli.call([*self._job_argv("stop"), f"--job-execution-name={handle.ref}"])
        if result is None:
            raise ContainerError(f"failed to stop {handle.ref}: az could not run")
        if result.returncode != 0 and not _mentions(result, _NOT_FOUND):
            raise ContainerError(f"failed to stop {handle.ref}: {result.detail}")

    def rm(self, handle: RemoteHandle) -> None:
        """Stop the execution; ACA keeps it only as history and the job is the customer's."""
        self.stop(handle)

    def freeze(self, handle: RemoteHandle) -> None:
        """Stop the execution; ACA has no suspend, the branch is the saved state."""
        self.stop(handle)

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Start afresh from the frozen execution's own template, marked as a resume.

        Raises:
            ContainerError: The frozen execution can no longer be read, so its
                template is unknown.
        """
        found = self._show(handle)
        containers = (found or {}).get("properties", {}).get("template", {}).get("containers")
        if not containers:
            raise ContainerError(f"cannot thaw {handle.ref}: the execution cannot be read")
        container = containers[0]
        env = {e["name"]: e["value"] for e in container.get("env", ()) if "value" in e}
        env[RESUME_ENV] = "1"
        command = [*container.get("command", ()), *container.get("args", ())]
        return self._start(container["image"], command, env)

    def _start(self, image: str, command: Sequence[str], env: Mapping[str, str]) -> RemoteHandle:
        cfg = self._cfg
        argv = [
            *self._job_argv("start"),
            f"--image={image}",
            f"--container-name={_CONTAINER_NAME}",
            f"--cpu={cfg.cpu}",
            f"--memory={cfg.memory}",
            "--query=name",
            "--output=tsv",
        ]
        if command:
            argv.append(f"--command={shlex.join(command)}")
        argv += [
            "--env-vars",
            *(f"{k}={v}" for k, v in env.items()),
            f"{WORKER_TOKEN_ENV}=secretref:{_TOKEN_SECRET_REF}",
        ]
        result = self._cli.call(argv)
        if result is None:
            raise ContainerError(f"failed to start ACA job {cfg.job}: az could not run")
        if result.returncode != 0:
            if _mentions(result, _REFUSED):
                raise ProvisionRefused(f"Azure refused job {cfg.job}: {result.detail}")
            raise ContainerError(f"failed to start ACA job {cfg.job}: {result.detail}")
        return RemoteHandle(runtime=self._name, ref=result.stdout.strip())

    def _show(self, handle: RemoteHandle) -> dict[str, Any] | None:
        """The execution resource, ``{}`` when it does not exist, ``None`` when unreadable."""
        result = self._cli.call(
            [
                *self._job_argv("execution", "show"),
                f"--job-execution-name={handle.ref}",
                "--output=json",
            ]
        )
        if result is None:
            return None
        if result.returncode != 0:
            return {} if _mentions(result, _NOT_FOUND) else None
        try:
            found = json.loads(result.stdout)
        except ValueError:
            return None
        return found if isinstance(found, dict) else None

    def _job_argv(self, *verb: str) -> list[str]:
        cfg = self._cfg
        return [
            cfg.az_bin,
            "containerapp",
            "job",
            *verb,
            f"--name={cfg.job}",
            f"--resource-group={cfg.resource_group}",
            f"--subscription={cfg.subscription}",
        ]


def _mentions(result: CliResult, needles: Sequence[str]) -> bool:
    text = f"{result.stderr}\n{result.stdout}"
    return any(n in text for n in needles)


__all__ = ["AcaJobDriver"]
