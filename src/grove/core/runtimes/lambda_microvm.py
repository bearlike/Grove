"""AWS Lambda MicroVMs as a resident workspace runtime, with NATIVE suspend and resume.

One MicroVM per workspace, launched through ``aws lambda-microvms``. Freeze is
``suspend-microvm`` and thaw is ``resume-microvm`` on the SAME MicroVM, memory
and disk kept, so thaw keeps the handle. Two launch facts are load-bearing: a
MicroVM ships with an inbound HTTPS endpoint by default, and Grove allows
nothing to dial in, so it runs with the ``NO_INGRESS`` connector; and the
platform's idle suspend keys on endpoint traffic, which a dial-out worker never
produces, so no idle policy is sent and Grove's own idle detection decides.

Image-level environment is shared by every MicroVM of an image, so the
per-workspace launch facts ride the ``/run`` hook payload instead.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from grove.core.config import LambdaMicrovmRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import (
    ProvisionRefused,
    RemoteHandle,
    RuntimeState,
    WorkspaceSpec,
)
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes._env import RuntimeEnv

_NOT_FOUND: Final = ("ResourceNotFoundException",)
_REFUSED: Final = ("ServiceQuotaExceededException", "ThrottlingException")
# The documented MicroVM lifecycle states, onto the contract's.
_STATES: Final[Mapping[str, RuntimeState]] = {
    "PENDING": RuntimeState.STARTING,
    "RUNNING": RuntimeState.RUNNING,
    "SUSPENDING": RuntimeState.FROZEN,
    "SUSPENDED": RuntimeState.FROZEN,
    "TERMINATING": RuntimeState.STOPPED,
    "TERMINATED": RuntimeState.STOPPED,
}
# The standard variables the aws CLI reads its credentials from.
_AWS_ACCESS_KEY: Final = "AWS_ACCESS_KEY_ID"
_AWS_SECRET_KEY: Final = "AWS_SECRET_ACCESS_KEY"


class LambdaMicrovmDriver:
    """Drives one ``runtimes.<name>`` MicroVM image through ``aws lambda-microvms``."""

    def __init__(
        self, name: str, cfg: LambdaMicrovmRuntime, *, cli: ProviderCli | None = None
    ) -> None:
        """Bind the driver to one configured image.

        Args:
            name: The ``runtimes.<name>`` key, stamped on every handle.
            cfg: That block's settings.
            cli: The ``aws`` process edge; built from ``cfg`` when omitted.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(
            timeout_seconds=cfg.cli_timeout_seconds, env=self._cli_env(cfg)
        )

    @staticmethod
    def _cli_env(cfg: LambdaMicrovmRuntime) -> dict[str, str]:
        """This process's environment with the configured credential names mapped onto aws's own."""
        env = dict(os.environ)
        for ours, theirs in (
            (cfg.access_key_env, _AWS_ACCESS_KEY),
            (cfg.secret_key_env, _AWS_SECRET_KEY),
        ):
            if env.get(ours):
                env[theirs] = env[ours]
        env["AWS_REGION"] = cfg.region
        return env

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """The image definition plus the CloudFormation the MicroVM needs.

        The customer builds the image with ``create-microvm-image`` from the
        runtime image (arm64) and deploys the role; the daemon token is a
        Secrets Manager ARN, never a value.
        """
        cfg = self._cfg
        document = {
            "image": {
                "identifier": cfg.image,
                "architecture": "arm64",
                "memoryInGb": cfg.memory_gb,
                "dockerfile": f"FROM {spec.image}\nENTRYPOINT {json.dumps(list(spec.command))}\n",
            },
            "cloudformation": {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Parameters": {"DaemonTokenSecretArn": {"Type": "String"}},
                "Resources": {
                    "GroveWorkerRole": {
                        "Type": "AWS::IAM::Role",
                        "Properties": {
                            "AssumeRolePolicyDocument": {
                                "Version": "2012-10-17",
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Principal": {"Service": "lambda.amazonaws.com"},
                                        "Action": "sts:AssumeRole",
                                    }
                                ],
                            },
                            "Policies": [
                                {
                                    "PolicyName": "grove-daemon-token",
                                    "PolicyDocument": {
                                        "Version": "2012-10-17",
                                        "Statement": [
                                            {
                                                "Effect": "Allow",
                                                "Action": "secretsmanager:GetSecretValue",
                                                "Resource": {"Ref": "DaemonTokenSecretArn"},
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                },
            },
        }
        return json.dumps(document, indent=2) + "\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Run a MicroVM for this workspace with no ingress and no platform idle policy."""
        payload = {
            "workspace_id": spec.workspace_id,
            "command": list(spec.command),
            "env": dict(spec.env),
            "daemon_token_secret_arn": RuntimeEnv(self._name).require(
                "daemon_token_secret_arn_env", self._cfg.daemon_token_secret_arn_env
            ),
        }
        result = self._run(payload)
        try:
            ref = json.loads(result.stdout)["microvmId"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ContainerError(
                f"run-microvm answered without a microvmId: {result.detail}"
            ) from exc
        return RemoteHandle(runtime=self._name, ref=ref)

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Map the MicroVM's lifecycle state; ``None`` when aws cannot answer."""
        result = self._cli.call([*self._argv("get-microvm"), f"--microvm-identifier={handle.ref}"])
        if result is None:
            return None
        if result.returncode != 0:
            return RuntimeState.ABSENT if _mentions(result, _NOT_FOUND) else None
        try:
            found = json.loads(result.stdout)
        except ValueError:
            return None
        return _STATES.get(found.get("state", "")) if isinstance(found, dict) else None

    def stop(self, handle: RemoteHandle) -> None:
        """Terminate the MicroVM; a MicroVM cannot be stopped and restarted."""
        self._mutate("terminate-microvm", handle)

    def rm(self, handle: RemoteHandle) -> None:
        """Terminate the MicroVM, which releases every resource it holds."""
        self._mutate("terminate-microvm", handle)

    def freeze(self, handle: RemoteHandle) -> None:
        """Suspend natively; memory and disk are kept, suspended time still counts."""
        self._mutate("suspend-microvm", handle)

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Resume the same MicroVM; the handle is unchanged."""
        self._mutate("resume-microvm", handle)
        return handle

    def _run(self, payload: Mapping[str, Any]) -> CliResult:
        cfg = self._cfg
        argv = [
            *self._argv("run-microvm"),
            f"--image-identifier={cfg.image}",
            f"--maximum-duration-in-seconds={cfg.max_duration_seconds}",
            f"--ingress-network-connectors={self._connector('NO_INGRESS')}",
            f"--egress-network-connectors={self._connector('INTERNET_EGRESS')}",
            f"--run-hook-payload={json.dumps(payload, separators=(',', ':'))}",
            "--output=json",
        ]
        if cfg.image_version:
            argv.append(f"--image-version={cfg.image_version}")
        if cfg.execution_role_arn:
            argv.append(f"--execution-role-arn={cfg.execution_role_arn}")
        result = self._cli.call(argv)
        if result is None:
            raise ContainerError(f"failed to run MicroVM from {cfg.image}: aws could not run")
        if result.returncode != 0:
            if _mentions(result, _REFUSED):
                raise ProvisionRefused(
                    f"Lambda refused a MicroVM from {cfg.image}: {result.detail}"
                )
            raise ContainerError(f"failed to run MicroVM from {cfg.image}: {result.detail}")
        return result

    def _mutate(self, verb: str, handle: RemoteHandle) -> None:
        result = self._cli.call([*self._argv(verb), f"--microvm-identifier={handle.ref}"])
        if result is None:
            raise ContainerError(f"failed to {verb} {handle.ref}: aws could not run")
        if result.returncode == 0 or (
            verb == "terminate-microvm" and _mentions(result, _NOT_FOUND)
        ):
            return
        if verb == "resume-microvm" and _mentions(result, _REFUSED):
            raise ProvisionRefused(f"Lambda refused to resume {handle.ref}: {result.detail}")
        raise ContainerError(f"failed to {verb} {handle.ref}: {result.detail}")

    def _connector(self, kind: str) -> str:
        return (
            f"arn:aws:lambda:{self._cfg.region}:aws:network-connector:aws-network-connector:{kind}"
        )

    def _argv(self, verb: str) -> list[str]:
        return [self._cfg.aws_bin, "lambda-microvms", verb, f"--region={self._cfg.region}"]


def _mentions(result: CliResult, needles: Sequence[str]) -> bool:
    text = f"{result.stderr}\n{result.stdout}"
    return any(n in text for n in needles)


__all__ = ["LambdaMicrovmDriver"]
