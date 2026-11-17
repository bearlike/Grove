"""The Cloud Run episodic driver against a fake service and a fake ``gcloud``."""

from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest

from grove.core.config import CloudRunEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.cloud_run_episodic import CloudRunEpisodicDriver
from tests.core.test_runtimes_contract import _SPEC, _TURN, assert_episodic_conformance

_CFG = CloudRunEpisodicRuntime(
    project="acme-dev",
    region="us-central1",
    service="grove-turn",
    url="https://grove-turn.example.run.app",
    image="registry.example.com/w:1",
)


class FakeGcloud(ProviderCli):
    def __init__(self, *, token: str = "tok") -> None:
        super().__init__(timeout_seconds=1)
        self.token = token

    def call(self, argv: Sequence[str]) -> CliResult | None:
        assert argv[1:3] == ["auth", "print-identity-token"]
        return CliResult(0, self.token + "\n", "") if self.token else CliResult(1, "", "no creds")


class FakeService:
    """Answers a turn the way the worker does, continuing the session it was handed."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="no available instance")
        turn = json.loads(request.content)
        session = turn["session_id"] or "sess-1"
        return httpx.Response(
            200, json={"session_id": session, "head_commit": "abc", "outcome": "done"}
        )


def _driver(service: FakeService, gcloud: FakeGcloud | None = None) -> CloudRunEpisodicDriver:
    http = httpx.Client(transport=httpx.MockTransport(service))
    return CloudRunEpisodicDriver("gcp", _CFG, cli=gcloud or FakeGcloud(), http=http)


def test_cloud_run_episodic_driver_conforms() -> None:
    assert_episodic_conformance(_driver(FakeService()))


def test_every_turn_carries_an_identity_token_for_the_service() -> None:
    service = FakeService()
    _driver(service).run_turn(_SPEC, _TURN)
    assert service.seen[0].headers["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("status", [429, 503])
def test_no_instance_is_provision_refused_and_not_retried(status: int) -> None:
    service = FakeService(status)
    with pytest.raises(ProvisionRefused):
        _driver(service).run_turn(_SPEC, _TURN)
    assert len(service.seen) == 1


def test_a_failed_turn_raises_and_never_synthesises_a_result() -> None:
    with pytest.raises(ContainerError):
        _driver(FakeService(500)).run_turn(_SPEC, _TURN)


def test_no_token_means_no_request() -> None:
    service = FakeService()
    with pytest.raises(ContainerError):
        _driver(service, FakeGcloud(token="")).run_turn(_SPEC, _TURN)
    assert service.seen == []


def test_emitted_service_scales_to_zero_and_bounds_the_turn() -> None:
    service = json.loads(_driver(FakeService()).emit_spec(_SPEC))
    template = service["spec"]["template"]
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/minScale"] == "0"
    assert template["spec"]["timeoutSeconds"] == 3600
