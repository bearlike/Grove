"""The Azure Container Apps job driver against a fake ``az`` that keeps executions in memory."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from itertools import count
from typing import Any

import pytest

from grove.core.config import AcaJobRuntime
from grove.core.runtimes import RESUME_ENV, ProvisionRefused, RemoteHandle
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.aca import AcaJobDriver
from tests.core.test_runtimes_contract import _SPEC, assert_driver_conformance

_CFG = AcaJobRuntime(
    subscription="00000000-0000-0000-0000-000000000000",
    resource_group="grove",
    environment="grove-env",
    job="grove-worker",
    image="acme.azurecr.io/grove/worker:1",
)


class FakeAz(ProviderCli):
    """Answers the ``az containerapp job`` subcommands the driver issues."""

    def __init__(self, *, refuse: bool = False) -> None:
        super().__init__(timeout_seconds=1)
        self.refuse = refuse
        self.calls: list[list[str]] = []
        self.executions: dict[str, dict[str, Any]] = {}
        self._ids = count(1)

    def call(self, argv: Sequence[str]) -> CliResult | None:
        argv = list(argv)
        self.calls.append(argv)
        flags = dict(a.split("=", 1) for a in argv if a.startswith("--") and "=" in a)
        if argv[3] == "start":
            if self.refuse:
                return CliResult(1, "", "(QuotaExceeded) Operation would exceed the core quota")
            name = f"grove-worker-{next(self._ids)}"
            env_args = argv[argv.index("--env-vars") + 1 :]
            env = [
                {"name": k, "value": v}
                for k, v in (e.split("=", 1) for e in env_args)
                if not v.startswith("secretref:")
            ]
            self.executions[name] = {
                "name": name,
                "properties": {
                    "status": "Running",
                    "template": {
                        "containers": [
                            {
                                "image": flags["--image"],
                                "command": shlex.split(flags.get("--command", "")),
                                "env": env,
                            }
                        ]
                    },
                },
            }
            return CliResult(0, name + "\n", "")
        name = flags["--job-execution-name"]
        found = self.executions.get(name)
        if found is None:
            return CliResult(1, "", f"(ResourceNotFound) The execution '{name}' was not found.")
        if argv[3] == "execution":
            return CliResult(0, json.dumps(found), "")
        found["properties"]["status"] = "Stopped"
        return CliResult(0, "", "")


def test_aca_job_driver_conforms() -> None:
    assert_driver_conformance(AcaJobDriver("azure", _CFG, cli=FakeAz()))


def test_quota_refusal_is_provision_refused_and_not_retried() -> None:
    az = FakeAz(refuse=True)
    with pytest.raises(ProvisionRefused):
        AcaJobDriver("azure", _CFG, cli=az).up(_SPEC)
    assert len(az.calls) == 1


def test_thaw_restarts_the_full_template_marked_as_a_resume() -> None:
    az = FakeAz()
    driver = AcaJobDriver("azure", _CFG, cli=az)
    handle = driver.up(_SPEC)
    driver.freeze(handle)
    thawed = driver.thaw(handle)
    container = az.executions[thawed.ref]["properties"]["template"]["containers"][0]
    assert container["image"] == _SPEC.image
    assert container["command"] == list(_SPEC.command)
    assert {e["name"]: e["value"] for e in container["env"]} == {**_SPEC.env, RESUME_ENV: "1"}


def test_unknown_status_word_and_unreadable_az_are_none() -> None:
    class Silent(FakeAz):
        def call(self, argv: Sequence[str]) -> CliResult | None:
            return None

    assert AcaJobDriver("azure", _CFG, cli=Silent()).status(RemoteHandle("azure", "x")) is None


def test_emitted_template_is_manual_never_retried_and_secret_by_reference() -> None:
    job = json.loads(AcaJobDriver("azure", _CFG, cli=FakeAz()).emit_spec(_SPEC))
    configuration = job["properties"]["configuration"]
    assert configuration["triggerType"] == "Manual"
    assert configuration["replicaRetryLimit"] == 0
    token = next(
        e
        for e in job["properties"]["template"]["containers"][0]["env"]
        if e["name"] == "GROVE_WORKER_TOKEN"
    )
    assert "value" not in token
