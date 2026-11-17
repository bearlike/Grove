"""The Cloud Run job driver against a fake ``gcloud`` that keeps executions in memory."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import count
from typing import Any

import pytest

from grove.core.config import CloudRunJobRuntime
from grove.core.runtimes import RESUME_ENV, ProvisionRefused, RemoteHandle
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.cloud_run import CloudRunJobDriver
from tests.core.test_runtimes_contract import _SPEC, assert_driver_conformance

_CFG = CloudRunJobRuntime(
    project="acme-dev", region="us-central1", job="grove-worker", image="registry.example.com/w:1"
)


class FakeGcloud(ProviderCli):
    """Answers the ``gcloud run jobs`` subcommands the driver issues."""

    def __init__(self, *, refuse: bool = False) -> None:
        super().__init__(timeout_seconds=1)
        self.refuse = refuse
        self.calls: list[list[str]] = []
        self.executions: dict[str, dict[str, Any]] = {}
        self._ids = count(1)

    def call(self, argv: Sequence[str]) -> CliResult | None:
        argv = list(argv)
        self.calls.append(argv)
        words = [a for a in argv[3:] if not a.startswith("-")]
        flags = dict(a.split("=", 1) for a in argv if a.startswith("--") and "=" in a)
        if words[0] == "execute":
            if self.refuse:
                return CliResult(1, "", "ERROR: RESOURCE_EXHAUSTED: Quota exceeded")
            name = f"grove-worker-{next(self._ids)}"
            env = _split(flags.get("--update-env-vars", ""))
            self.executions[name] = {
                "metadata": {"name": name},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "env": [
                                        {"name": k, "value": v}
                                        for k, v in (e.split("=", 1) for e in env)
                                    ],
                                    "args": _split(flags.get("--args", "")),
                                }
                            ]
                        }
                    }
                },
                "status": {"runningCount": 1},
            }
            return CliResult(0, name + "\n", "")
        name = words[2]
        found = self.executions.get(name)
        if found is None:
            return CliResult(1, "", f"ERROR: NOT_FOUND: Execution [{name}] could not be found.")
        if words[1] == "describe":
            return CliResult(0, json.dumps(found), "")
        if words[1] == "cancel":
            found["status"] = {"cancelledCount": 1, "completionTime": "2026-10-22T00:00:00Z"}
        elif words[1] == "delete":
            del self.executions[name]
        return CliResult(0, "", "")


def _split(value: str) -> list[str]:
    if not value:
        return []
    if value.startswith("^"):
        delim, rest = value[1], value[3:]
        return rest.split(delim)
    return value.split(",")


def test_cloud_run_job_driver_conforms() -> None:
    assert_driver_conformance(CloudRunJobDriver("gcp", _CFG, cli=FakeGcloud()))


def test_quota_refusal_is_provision_refused_and_not_retried() -> None:
    gcloud = FakeGcloud(refuse=True)
    with pytest.raises(ProvisionRefused):
        CloudRunJobDriver("gcp", _CFG, cli=gcloud).up(_SPEC)
    assert len(gcloud.calls) == 1


def test_thaw_re_executes_with_the_frozen_overrides_marked_as_a_resume() -> None:
    gcloud = FakeGcloud()
    driver = CloudRunJobDriver("gcp", _CFG, cli=gcloud)
    spec = type(_SPEC)(
        workspace_id=_SPEC.workspace_id,
        image=_SPEC.image,
        command=["grove-native-worker", "--config", "a,b"],
        env={"GROVE_WORKSPACE_ID": _SPEC.workspace_id},
    )
    handle = driver.up(spec)
    driver.freeze(handle)
    thawed = driver.thaw(handle)
    container = gcloud.executions[thawed.ref]["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env == {"GROVE_WORKSPACE_ID": _SPEC.workspace_id, RESUME_ENV: "1"}
    assert container["args"] == ["--config", "a,b"]


def test_unreadable_gcloud_reads_as_unknown_never_absent() -> None:
    class Silent(FakeGcloud):
        def call(self, argv: Sequence[str]) -> CliResult | None:
            return None

    driver = CloudRunJobDriver("gcp", _CFG, cli=Silent())
    assert driver.status(RemoteHandle(runtime="gcp", ref="grove-worker-1")) is None


def test_emitted_job_never_retries_and_carries_no_secret_value() -> None:
    job = json.loads(CloudRunJobDriver("gcp", _CFG, cli=FakeGcloud()).emit_spec(_SPEC))
    task = job["spec"]["template"]["spec"]["template"]["spec"]
    assert task["maxRetries"] == 0
    token = next(e for e in task["containers"][0]["env"] if e["name"] == "GROVE_WORKER_TOKEN")
    assert (
        "value" not in token and token["valueFrom"]["secretKeyRef"]["name"] == "grove-daemon-token"
    )
