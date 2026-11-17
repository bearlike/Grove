"""The Lambda MicroVM driver against a fake ``aws`` that keeps MicroVMs in memory."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import count

import pytest

from grove.core.config import LambdaMicrovmRuntime
from grove.core.runtimes import ProvisionRefused, RemoteHandle, RuntimeState
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.lambda_microvm import LambdaMicrovmDriver
from tests.core.test_runtimes_contract import _SPEC, assert_driver_conformance

_CFG = LambdaMicrovmRuntime(
    region="us-east-1", image="arn:aws:lambda:us-east-1:123456789012:microvm-image:grove-worker"
)


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_CFG`` names the daemon token ARN's variable; an unset one is refused (#1049)."""
    monkeypatch.setenv("GROVE_DAEMON_TOKEN_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:t")


_NEXT: dict[str, str] = {
    "suspend-microvm": "SUSPENDED",
    "resume-microvm": "RUNNING",
    "terminate-microvm": "TERMINATED",
}


class FakeAws(ProviderCli):
    """Answers the ``aws lambda-microvms`` verbs the driver issues."""

    def __init__(self, *, refuse: str = "") -> None:
        super().__init__(timeout_seconds=1)
        self.refuse = refuse
        self.calls: list[list[str]] = []
        self.states: dict[str, str] = {}
        self._ids = count(1)

    def call(self, argv: Sequence[str]) -> CliResult | None:
        argv = list(argv)
        self.calls.append(argv)
        verb = argv[2]
        flags = dict(a.split("=", 1) for a in argv if a.startswith("--") and "=" in a)
        if verb == "run-microvm":
            if self.refuse:
                return CliResult(
                    254, "", f"An error occurred ({self.refuse}) when calling RunMicrovm"
                )
            ref = f"mvm-{next(self._ids)}"
            self.states[ref] = "RUNNING"
            return CliResult(0, json.dumps({"microvmId": ref, "endpoint": "x"}), "")
        ref = flags["--microvm-identifier"]
        if ref not in self.states:
            return CliResult(254, "", "An error occurred (ResourceNotFoundException)")
        if verb == "get-microvm":
            return CliResult(0, json.dumps({"microvmId": ref, "state": self.states[ref]}), "")
        self.states[ref] = _NEXT[verb]
        return CliResult(0, "{}", "")


def test_lambda_microvm_driver_conforms() -> None:
    assert_driver_conformance(LambdaMicrovmDriver("aws", _CFG, cli=FakeAws()))


@pytest.mark.parametrize("code", ["ServiceQuotaExceededException", "ThrottlingException"])
def test_quota_or_throttle_is_provision_refused_and_not_retried(code: str) -> None:
    aws = FakeAws(refuse=code)
    with pytest.raises(ProvisionRefused):
        LambdaMicrovmDriver("aws", _CFG, cli=aws).up(_SPEC)
    assert len(aws.calls) == 1


def test_launch_has_no_ingress_no_idle_policy_and_carries_the_workspace() -> None:
    aws = FakeAws()
    LambdaMicrovmDriver("aws", _CFG, cli=aws).up(_SPEC)
    run = aws.calls[0]
    assert any(
        a.startswith("--ingress-network-connectors=") and a.endswith(":NO_INGRESS") for a in run
    )
    assert not any(a.startswith("--idle-policy") for a in run)
    payload = json.loads(
        next(a for a in run if a.startswith("--run-hook-payload=")).split("=", 1)[1]
    )
    assert payload["workspace_id"] == _SPEC.workspace_id


def test_freeze_is_native_suspend_and_thaw_keeps_the_handle() -> None:
    driver = LambdaMicrovmDriver("aws", _CFG, cli=FakeAws())
    handle = driver.up(_SPEC)
    driver.freeze(handle)
    assert driver.status(handle) is RuntimeState.FROZEN
    assert driver.thaw(handle) == handle


def test_unreadable_aws_reads_as_unknown() -> None:
    class Silent(FakeAws):
        def call(self, argv: Sequence[str]) -> CliResult | None:
            return None

    assert (
        LambdaMicrovmDriver("aws", _CFG, cli=Silent()).status(RemoteHandle("aws", "mvm-1")) is None
    )
