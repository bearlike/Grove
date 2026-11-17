"""Cloud Run services as an EPISODIC runtime: one authenticated request is one turn.

The service is the customer's (they apply the emitted definition with ``gcloud
run services replace``, invokers restricted to authenticated callers). Grove's
own outbound POST carries the turn; the worker inside restores the worktree
from the branch, runs exactly one turn, pushes, and answers with the result.
A request can run at most 60 minutes, so a turn must fit inside one.

The invoker credential is an identity token minted by ``gcloud auth
print-identity-token``, so no Google SDK is a dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Final

import httpx

from grove.core.config import CloudRunEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import (
    ProvisionRefused,
    TurnRequest,
    TurnResult,
    WorkspaceSpec,
)
from grove.core.runtimes._cli import ProviderCli
from grove.core.runtimes._http import response_detail

# gcloud reads an alternate credential file from this variable.
_GCLOUD_CREDENTIAL_ENV: Final = "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"
# Cloud Run's answer when it has no instance to give the request.
_REFUSED_STATUSES: Final = frozenset({429, 503})


class CloudRunEpisodicDriver:
    """Runs turns as requests to one ``runtimes.<name>`` Cloud Run service."""

    def __init__(
        self,
        name: str,
        cfg: CloudRunEpisodicRuntime,
        *,
        cli: ProviderCli | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        """Bind the driver to one configured service.

        Args:
            name: The ``runtimes.<name>`` key.
            cfg: That block's settings.
            cli: The ``gcloud`` edge that mints the identity token; built from
                ``cfg`` when omitted.
            http: The client a turn is POSTed through; its timeout is the
                service's request timeout, since a turn may legitimately take
                all of it.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(
            timeout_seconds=cfg.cli_timeout_seconds, env=self._cli_env(cfg)
        )
        self._http = http or httpx.Client(timeout=cfg.request_timeout_seconds)

    @staticmethod
    def _cli_env(cfg: CloudRunEpisodicRuntime) -> dict[str, str]:
        env = dict(os.environ)
        credential = env.get(cfg.credentials_env)
        if credential:
            env[_GCLOUD_CREDENTIAL_ENV] = credential
        return env

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """A ``serving.knative.dev/v1`` Service scaled to zero between turns.

        JSON, which ``gcloud run services replace`` reads as YAML. Who may
        invoke it is IAM, not part of this document: deploy it with
        ``--no-allow-unauthenticated`` and grant ``run.invoker`` to the
        credential ``credentials_env`` names.
        """
        cfg = self._cfg
        service = {
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "metadata": {"name": cfg.service},
            "spec": {
                "template": {
                    "metadata": {"annotations": {"autoscaling.knative.dev/minScale": "0"}},
                    "spec": {
                        "timeoutSeconds": cfg.request_timeout_seconds,
                        "containerConcurrency": 1,
                        "containers": [
                            {
                                "image": spec.image,
                                "command": list(spec.command[:1]),
                                "args": list(spec.command[1:]),
                                "env": [{"name": k, "value": v} for k, v in spec.env.items()],
                            }
                        ],
                    },
                }
            },
        }
        return json.dumps(service, indent=2) + "\n"

    def run_turn(self, spec: WorkspaceSpec, turn: TurnRequest) -> TurnResult:
        """POST the turn and decode the worker's answer.

        Raises:
            ProvisionRefused: Cloud Run had no instance for the request.
            ContainerError: The token could not be minted, or the request failed
                or was cut short before the worker answered.
        """
        del spec
        response = self._post(turn)
        if response.status_code in _REFUSED_STATUSES:
            raise ProvisionRefused(
                f"Cloud Run refused a turn on {self._cfg.service}: {response_detail(response)}"
            )
        if response.status_code != 200:
            raise ContainerError(
                f"turn on {self._cfg.service} failed ({response.status_code}): "
                f"{response_detail(response)}"
            )
        try:
            return TurnResult.from_wire(response.json())
        except ValueError as exc:
            raise ContainerError(f"turn on {self._cfg.service} answered no JSON") from exc

    def _post(self, turn: TurnRequest) -> httpx.Response:
        try:
            return self._http.post(
                self._cfg.url,
                json=asdict(turn),
                headers={"Authorization": f"Bearer {self._identity_token()}"},
            )
        except httpx.HTTPError as exc:
            raise ContainerError(f"turn on {self._cfg.service} did not complete: {exc}") from exc

    def _identity_token(self) -> str:
        """An identity token for the service's own URL, minted by gcloud per turn."""
        result = self._cli.call(
            [self._cfg.gcloud_bin, "auth", "print-identity-token", f"--audiences={self._cfg.url}"]
        )
        if result is None or result.returncode != 0 or not result.stdout.strip():
            detail = "gcloud could not run" if result is None else result.detail
            raise ContainerError(f"cannot invoke {self._cfg.service}: {detail}")
        return result.stdout.strip()


__all__ = ["CloudRunEpisodicDriver"]
