"""The Azure Functions on Container Apps episodic driver against a fake app and ``az``."""

from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest

from grove.core.config import AcaFunctionsEpisodicRuntime
from grove.core.errors import ContainerError
from grove.core.runtimes import ProvisionRefused
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes.aca_functions_episodic import AcaFunctionsEpisodicDriver
from tests.core.test_runtimes_contract import _SPEC, _TURN, assert_episodic_conformance

_CFG = AcaFunctionsEpisodicRuntime(
    subscription="00000000-0000-0000-0000-000000000000",
    resource_group="grove",
    environment="grove-env",
    app="grove-turn",
    url="https://grove-turn.example.azurecontainerapps.io/api/turn",
    image="acme.azurecr.io/grove/worker:1",
    token_resource="api://grove-turn",
)


class FakeAz(ProviderCli):
    def __init__(self, *, token: str = "tok") -> None:
        super().__init__(timeout_seconds=1)
        self.token = token

    def call(self, argv: Sequence[str]) -> CliResult | None:
        assert list(argv[1:3]) == ["account", "get-access-token"]
        return CliResult(0, self.token + "\n", "") if self.token else CliResult(1, "", "AADSTS")


class FakeApp:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="no replica")
        session = json.loads(request.content)["session_id"] or "sess-1"
        result = {"session_id": session, "head_commit": "abc", "outcome": "done"}
        return httpx.Response(200, json=result)


def _driver(app: FakeApp, az: FakeAz | None = None) -> AcaFunctionsEpisodicDriver:
    http = httpx.Client(transport=httpx.MockTransport(app))
    return AcaFunctionsEpisodicDriver("azure", _CFG, cli=az or FakeAz(), http=http)


def test_aca_functions_episodic_driver_conforms() -> None:
    assert_episodic_conformance(_driver(FakeApp()))


@pytest.mark.parametrize("status", [429, 503])
def test_no_replica_is_provision_refused_and_not_retried(status: int) -> None:
    app = FakeApp(status)
    with pytest.raises(ProvisionRefused):
        _driver(app).run_turn(_SPEC, _TURN)
    assert len(app.seen) == 1


def test_no_token_means_no_request() -> None:
    app = FakeApp()
    with pytest.raises(ContainerError):
        _driver(app, FakeAz(token="")).run_turn(_SPEC, _TURN)
    assert app.seen == []


def test_emitted_app_is_a_function_app_scaled_to_zero() -> None:
    app = json.loads(_driver(FakeApp()).emit_spec(_SPEC))
    assert app["kind"] == "functionapp"
    assert app["properties"]["template"]["scale"]["minReplicas"] == 0
