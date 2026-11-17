"""Azure Functions on Container Apps as an EPISODIC runtime: one HTTP request is one turn.

The function app is the customer's (applied from the emitted template with
``az containerapp create --yaml``). Grove POSTs each turn with an Entra ID token
minted by ``az account get-access-token``, so no Azure SDK is a dependency. The
app has no documented long-running guarantee, so a turn must fit one request,
and platform maintenance may end a replica (SIGTERM, SIGKILL after 30 s).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Final

import httpx

from grove.core.config import AcaFunctionsEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused, TurnRequest, TurnResult, WorkspaceSpec
from grove.core.runtimes._cli import ProviderCli
from grove.core.runtimes._http import response_detail

# The app's answer when it has no replica for the request.
_REFUSED_STATUSES: Final = frozenset({429, 503})


class AcaFunctionsEpisodicDriver:
    """Runs turns as requests to one ``runtimes.<name>`` function app."""

    def __init__(
        self,
        name: str,
        cfg: AcaFunctionsEpisodicRuntime,
        *,
        cli: ProviderCli | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        """Bind the driver to one configured function app.

        Args:
            name: The ``runtimes.<name>`` key.
            cfg: That block's settings.
            cli: The ``az`` edge that mints the token; built from ``cfg`` when omitted.
            http: The client a turn is POSTed through, timed out at the turn's limit.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(timeout_seconds=cfg.cli_timeout_seconds, env=os.environ)
        self._http = http or httpx.Client(timeout=cfg.request_timeout_seconds)

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """A ``kind: functionapp`` Container App for ``az containerapp create --yaml``.

        JSON, which ``az`` reads as YAML.
        """
        cfg = self._cfg
        app = {
            "kind": "functionapp",
            "properties": {
                "environmentId": cfg.environment,
                "configuration": {"ingress": {"external": True, "targetPort": 80}},
                "template": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": spec.image,
                            "command": list(spec.command),
                            "env": [{"name": k, "value": v} for k, v in spec.env.items()],
                        }
                    ],
                    "scale": {"minReplicas": 0},
                },
            },
        }
        return json.dumps(app, indent=2) + "\n"

    def run_turn(self, spec: WorkspaceSpec, turn: TurnRequest) -> TurnResult:
        """POST the turn and decode the worker's answer.

        Raises:
            ProvisionRefused: The app had no replica for the request.
            ContainerError: The token could not be minted, or the request failed
                or was cut short before the worker answered.
        """
        del spec
        try:
            response = self._http.post(
                self._cfg.url,
                json=asdict(turn),
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
        except httpx.HTTPError as exc:
            raise ContainerError(f"turn on {self._cfg.app} did not complete: {exc}") from exc
        if response.status_code in _REFUSED_STATUSES:
            raise ProvisionRefused(
                f"Azure refused a turn on {self._cfg.app}: {response_detail(response)}"
            )
        if response.status_code != 200:
            detail = response_detail(response)
            raise ContainerError(f"turn on {self._cfg.app} failed: {detail}")
        try:
            return TurnResult.from_wire(response.json())
        except ValueError as exc:
            raise ContainerError(f"turn on {self._cfg.app} answered no JSON") from exc

    def _access_token(self) -> str:
        """An Entra ID token for the function's own application, minted per turn."""
        result = self._cli.call(
            [
                self._cfg.az_bin,
                "account",
                "get-access-token",
                f"--resource={self._cfg.token_resource}",
                f"--subscription={self._cfg.subscription}",
                "--query=accessToken",
                "--output=tsv",
            ]
        )
        if result is None or result.returncode != 0 or not result.stdout.strip():
            detail = "az could not run" if result is None else result.detail
            raise ContainerError(f"cannot invoke {self._cfg.app}: {detail}")
        return result.stdout.strip()


__all__ = ["AcaFunctionsEpisodicDriver"]
