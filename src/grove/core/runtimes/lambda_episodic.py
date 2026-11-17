"""Classic AWS Lambda as an EPISODIC runtime: one synchronous invocation is one turn.

``aws lambda invoke`` (``RequestResponse``) carries the turn; the function
restores the worktree from the branch, runs one turn, pushes and returns the
result. A function lives at most 900 s and gets no SIGTERM, so the worker must
push before its own deadline. The synchronous response is capped at 6 MB, which
is why the result is small and the branch carries the work.

The invoke writes the payload to a file and prints the call's metadata, which
is where a function error is reported (``FunctionError`` with exit status 0),
so the two are read separately.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from grove.core.config import LambdaEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused, TurnRequest, TurnResult, WorkspaceSpec
from grove.core.runtimes._cli import ProviderCli

_REFUSED: Final = ("TooManyRequestsException", "ThrottlingException", "ResourceConflictException")
# The standard variables the aws CLI reads its credentials from.
_AWS_ACCESS_KEY: Final = "AWS_ACCESS_KEY_ID"
_AWS_SECRET_KEY: Final = "AWS_SECRET_ACCESS_KEY"
# Classic Lambda's hard ceiling for one invocation.
_MAX_TIMEOUT_SECONDS: Final = 900


class LambdaEpisodicDriver:
    """Runs turns as invocations of one ``runtimes.<name>`` Lambda function."""

    def __init__(
        self, name: str, cfg: LambdaEpisodicRuntime, *, cli: ProviderCli | None = None
    ) -> None:
        """Bind the driver to one configured function.

        Args:
            name: The ``runtimes.<name>`` key.
            cfg: That block's settings.
            cli: The ``aws`` process edge; built from ``cfg`` when omitted.
        """
        self._name = name
        self._cfg = cfg
        self._cli = cli or ProviderCli(
            timeout_seconds=cfg.cli_timeout_seconds, env=self._cli_env(cfg)
        )

    @staticmethod
    def _cli_env(cfg: LambdaEpisodicRuntime) -> dict[str, str]:
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
        """A CloudFormation ``AWS::Lambda::Function`` running the runtime image.

        JSON, which SAM and CloudFormation both accept.
        """
        cfg = self._cfg
        template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Parameters": {"ExecutionRoleArn": {"Type": "String"}},
            "Resources": {
                "GroveTurn": {
                    "Type": "AWS::Lambda::Function",
                    "Properties": {
                        "FunctionName": cfg.function,
                        "PackageType": "Image",
                        "Code": {"ImageUri": spec.image},
                        "ImageConfig": {"EntryPoint": list(spec.command)},
                        "Role": {"Ref": "ExecutionRoleArn"},
                        "Timeout": _MAX_TIMEOUT_SECONDS,
                        "MemorySize": cfg.memory_mb,
                        "EphemeralStorage": {"Size": cfg.ephemeral_mb},
                        "Architectures": ["arm64"],
                        "Environment": {"Variables": dict(spec.env)},
                    },
                }
            },
        }
        return json.dumps(template, indent=2) + "\n"

    def run_turn(self, spec: WorkspaceSpec, turn: TurnRequest) -> TurnResult:
        """Invoke the function synchronously and decode what it returned.

        Raises:
            ProvisionRefused: Lambda throttled the invocation.
            ContainerError: The invoke failed, the function errored, or it timed out.
        """
        del spec
        with tempfile.TemporaryDirectory(prefix="grove-turn-") as scratch:
            out = Path(scratch) / "payload.json"
            metadata = self._invoke(turn, out)
            if metadata.get("FunctionError"):
                detail = out.read_text() if out.exists() else ""
                raise ContainerError(
                    f"turn on {self._cfg.function} failed ({metadata['FunctionError']}): {detail}"
                )
            try:
                return TurnResult.from_wire(json.loads(out.read_text()))
            except (OSError, ValueError) as exc:
                raise ContainerError(f"turn on {self._cfg.function} returned no result") from exc

    def _invoke(self, turn: TurnRequest, out: Path) -> dict[str, Any]:
        result = self._cli.call(
            [
                self._cfg.aws_bin,
                "lambda",
                "invoke",
                f"--function-name={self._cfg.function}",
                "--invocation-type=RequestResponse",
                "--cli-binary-format=raw-in-base64-out",
                f"--payload={json.dumps(asdict(turn))}",
                f"--region={self._cfg.region}",
                "--output=json",
                str(out),
            ]
        )
        if result is None:
            raise ContainerError(f"failed to invoke {self._cfg.function}: aws could not run")
        if result.returncode != 0:
            if any(code in result.detail for code in _REFUSED):
                raise ProvisionRefused(
                    f"Lambda refused a turn on {self._cfg.function}: {result.detail}"
                )
            raise ContainerError(f"failed to invoke {self._cfg.function}: {result.detail}")
        try:
            metadata = json.loads(result.stdout)
        except ValueError:
            metadata = {}
        return metadata if isinstance(metadata, dict) else {}


__all__ = ["LambdaEpisodicDriver"]
