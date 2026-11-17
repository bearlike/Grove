"""Cloud Run jobs as a resident workspace runtime: one execution per workspace.

The job is the customer's (they apply the emitted definition with
``gcloud run jobs replace``); a workspace is one EXECUTION of it, started with
per-execution env and args overrides. Cloud Run has no suspend, so ``freeze``
cancels the execution and ``thaw`` starts a fresh one from the pushed branch,
which is why ``thaw`` returns a new handle.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from grove.core.config import CloudRunJobRuntime
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

# gcloud's own words for "no such execution" and for a quota or capacity refusal.
_NOT_FOUND: Final = ("NOT_FOUND", "could not be found", "not found")
_REFUSED: Final = ("RESOURCE_EXHAUSTED", "Quota exceeded", "quota")
# gcloud reads an alternate list delimiter from a `^<delim>^` prefix
# (`gcloud topic escaping`); the first of these absent from every item is used.
_DELIMITERS: Final = ("@", "|", "~", "#", "%")
# The variable gcloud itself reads a credential file override from.
_GCLOUD_CREDENTIAL_ENV: Final = "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"


#: Cloud Run's documented cap on one container's environment variables, total.
_ENV_LIMIT_BYTES: Final = 32 * 1024


class CloudRunJobDriver:
    """Drives one ``runtimes.<name>`` Cloud Run job through ``gcloud``."""

    def __init__(
        self, name: str, cfg: CloudRunJobRuntime, *, cli: ProviderCli | None = None
    ) -> None:
        """Bind the driver to one configured job.

        Args:
            name: The ``runtimes.<name>`` key, stamped on every handle.
            cfg: That block's settings.
            cli: The ``gcloud`` process edge; built from ``cfg`` when omitted.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(
            timeout_seconds=cfg.cli_timeout_seconds, env=self._cli_env(cfg)
        )

    @staticmethod
    def _cli_env(cfg: CloudRunJobRuntime) -> dict[str, str]:
        """This process's environment, plus gcloud's credential override when one is named."""
        env = dict(os.environ)
        credential = env.get(cfg.credentials_env)
        if credential:
            env[_GCLOUD_CREDENTIAL_ENV] = credential
        return env

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """A ``run.googleapis.com/v1`` Job for ``gcloud run jobs replace``.

        JSON, which every YAML reader accepts, so no YAML dependency is needed.

        Raises:
            ProvisionRefused: The environment is over Cloud Run's limit.
        """
        spec.require_env_within(_ENV_LIMIT_BYTES, "Cloud Run")
        env: list[dict[str, Any]] = [{"name": k, "value": v} for k, v in spec.env.items()]
        env.append(
            {
                "name": WORKER_TOKEN_ENV,
                "valueFrom": {
                    "secretKeyRef": {"name": self._cfg.daemon_token_secret, "key": "latest"}
                },
            }
        )
        container: dict[str, Any] = {
            "image": spec.image,
            "command": list(spec.command[:1]),
            "args": list(spec.command[1:]),
            "env": env,
            "resources": {"limits": {"cpu": self._cfg.cpu, "memory": self._cfg.memory}},
        }
        job = {
            "apiVersion": "run.googleapis.com/v1",
            "kind": "Job",
            "metadata": {"name": self._cfg.job},
            "spec": {
                "template": {
                    "spec": {
                        "taskCount": 1,
                        "template": {
                            "spec": {
                                "maxRetries": 0,
                                "timeoutSeconds": self._cfg.timeout_seconds,
                                "containers": [container],
                            }
                        },
                    }
                }
            },
        }
        return json.dumps(job, indent=2) + "\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Execute the job once for this workspace; the execution name is the handle."""
        spec.require_env_within(_ENV_LIMIT_BYTES, "Cloud Run")
        return self._execute(spec.env, spec.command[1:])

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Map the execution's counters onto a state; ``None`` when gcloud cannot answer."""
        found = self._describe(handle)
        if found is None:
            return None
        if not found:
            return RuntimeState.ABSENT
        status = found.get("status", {})
        if status.get("completionTime") or any(
            status.get(k) for k in ("succeededCount", "failedCount", "cancelledCount")
        ):
            return RuntimeState.STOPPED
        return RuntimeState.RUNNING if status.get("runningCount") else RuntimeState.STARTING

    def stop(self, handle: RemoteHandle) -> None:
        """Cancel the execution."""
        self._mutate("cancel", handle)

    def rm(self, handle: RemoteHandle) -> None:
        """Delete the execution; the job is the customer's and stays."""
        self._mutate("delete", handle)

    def freeze(self, handle: RemoteHandle) -> None:
        """Cancel the execution; Cloud Run has no suspend, the branch is the saved state."""
        self.stop(handle)

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Execute afresh with the frozen execution's overrides, marked as a resume.

        Raises:
            ContainerError: The frozen execution can no longer be read, so its
                overrides are unknown.
        """
        found = self._describe(handle)
        if not found:
            raise ContainerError(f"cannot thaw {handle.ref}: the execution cannot be read")
        containers = found.get("spec", {}).get("template", {}).get("spec", {}).get("containers")
        container = containers[0] if containers else {}
        env = {e["name"]: e["value"] for e in container.get("env", ()) if "value" in e}
        env[RESUME_ENV] = "1"
        return self._execute(env, container.get("args", ()))

    def _execute(self, env: Mapping[str, str], args: Sequence[str]) -> RemoteHandle:
        argv = [
            *self._job_argv("execute"),
            self._cfg.job,
            "--async",
            "--format=value(metadata.name)",
        ]
        if env:
            argv.append("--update-env-vars=" + _gcloud_list([f"{k}={v}" for k, v in env.items()]))
        if args:
            argv.append("--args=" + _gcloud_list(list(args)))
        result = self._cli.call(argv)
        if result is None:
            raise ContainerError(
                f"failed to execute Cloud Run job {self._cfg.job}: gcloud could not run"
            )
        if result.returncode != 0:
            if _mentions(result, _REFUSED):
                raise ProvisionRefused(f"Cloud Run refused job {self._cfg.job}: {result.detail}")
            raise ContainerError(
                f"failed to execute Cloud Run job {self._cfg.job}: {result.detail}"
            )
        return RemoteHandle(runtime=self._name, ref=result.stdout.strip())

    def _describe(self, handle: RemoteHandle) -> dict[str, Any] | None:
        """The execution resource, ``{}`` when it does not exist, ``None`` when unreadable."""
        result = self._cli.call(
            [*self._job_argv("executions", "describe"), handle.ref, "--format=json"]
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

    def _mutate(self, verb: str, handle: RemoteHandle) -> None:
        """Run a cancel or delete; an execution that is already gone counts as done."""
        result = self._cli.call([*self._job_argv("executions", verb), handle.ref, "--quiet"])
        if result is None:
            raise ContainerError(f"failed to {verb} {handle.ref}: gcloud could not run")
        if result.returncode != 0 and not _mentions(result, _NOT_FOUND):
            raise ContainerError(f"failed to {verb} {handle.ref}: {result.detail}")

    def _job_argv(self, *verb: str) -> list[str]:
        cfg = self._cfg
        return [
            cfg.gcloud_bin,
            "run",
            "jobs",
            *verb,
            f"--project={cfg.project}",
            f"--region={cfg.region}",
        ]


def _mentions(result: CliResult, needles: Sequence[str]) -> bool:
    text = f"{result.stderr}\n{result.stdout}"
    return any(n in text for n in needles)


def _gcloud_list(items: Sequence[str]) -> str:
    """A gcloud list argument, switching delimiter when an item contains a comma.

    Raises:
        ContainerError: Every candidate delimiter occurs in some item.
    """
    if not any("," in item for item in items):
        return ",".join(items)
    for delim in _DELIMITERS:
        if not any(delim in item for item in items):
            return f"^{delim}^" + delim.join(items)
    raise ContainerError("cannot pass these values to gcloud: every list delimiter occurs in them")


__all__ = ["CloudRunJobDriver"]
