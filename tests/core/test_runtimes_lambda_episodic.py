"""The classic Lambda episodic driver against a fake ``aws lambda invoke``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from grove.core.config import LambdaEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.lambda_episodic import LambdaEpisodicDriver
from tests.core.test_runtimes_contract import _SPEC, _TURN, assert_episodic_conformance

_CFG = LambdaEpisodicRuntime(
    region="us-east-1", function="grove-turn", image="123.dkr.ecr.us-east-1.amazonaws.com/w:1"
)


class FakeAws(ProviderCli):
    """Writes the function's answer to the outfile and prints the call's metadata, like aws."""

    def __init__(self, *, refuse: str = "", function_error: str = "") -> None:
        super().__init__(timeout_seconds=1)
        self.refuse = refuse
        self.function_error = function_error
        self.calls: list[list[str]] = []

    def call(self, argv: Sequence[str]) -> CliResult | None:
        argv = list(argv)
        self.calls.append(argv)
        if self.refuse:
            return CliResult(
                254, "", f"An error occurred ({self.refuse}) when calling the Invoke operation"
            )
        payload = json.loads(next(a for a in argv if a.startswith("--payload=")).split("=", 1)[1])
        out = Path(argv[-1])
        if self.function_error:
            out.write_text(json.dumps({"errorMessage": "Task timed out after 900.00 seconds"}))
            return CliResult(0, json.dumps({"StatusCode": 200, "FunctionError": "Unhandled"}), "")
        session = payload["session_id"] or "sess-1"
        out.write_text(json.dumps({"session_id": session, "head_commit": "abc", "outcome": "done"}))
        return CliResult(0, json.dumps({"StatusCode": 200, "ExecutedVersion": "$LATEST"}), "")


def test_lambda_episodic_driver_conforms() -> None:
    assert_episodic_conformance(LambdaEpisodicDriver("aws", _CFG, cli=FakeAws()))


@pytest.mark.parametrize("code", ["TooManyRequestsException", "ThrottlingException"])
def test_throttle_is_provision_refused_and_not_retried(code: str) -> None:
    aws = FakeAws(refuse=code)
    with pytest.raises(ProvisionRefused):
        LambdaEpisodicDriver("aws", _CFG, cli=aws).run_turn(_SPEC, _TURN)
    assert len(aws.calls) == 1


def test_a_function_error_with_exit_zero_raises_and_never_synthesises_a_result() -> None:
    with pytest.raises(ContainerError, match="Unhandled"):
        LambdaEpisodicDriver("aws", _CFG, cli=FakeAws(function_error="Unhandled")).run_turn(
            _SPEC, _TURN
        )


def test_emitted_function_is_an_image_capped_at_fifteen_minutes() -> None:
    template = json.loads(LambdaEpisodicDriver("aws", _CFG, cli=FakeAws()).emit_spec(_SPEC))
    props = template["Resources"]["GroveTurn"]["Properties"]
    assert (props["PackageType"], props["Timeout"]) == ("Image", 900)
    assert props["EphemeralStorage"]["Size"] == 10240
