"""The OpenFaaS driver against an in-memory gateway behind ``httpx.MockTransport``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from grove.core.config import OpenFaasRuntime
from grove.core.runtimes import RESUME_ENV, ProvisionRefused, RemoteHandle, RuntimeState
from grove.core.runtimes.openfaas import OpenFaasDriver
from tests.core.test_runtimes_contract import _SPEC, assert_driver_conformance

_CFG = OpenFaasRuntime(gateway_url="https://faas.example.com", image="registry.example.com/w:1")


class FakeGateway:
    """The ``/system`` routes the driver calls, holding functions in memory."""

    def __init__(self, *, overloaded: bool = False) -> None:
        self.overloaded = overloaded
        self.functions: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/system/functions":
            return self._functions(request.method, body)
        return self._function(request.url.path, body)

    def _functions(self, method: str, body: dict[str, Any]) -> httpx.Response:
        if method in ("PUT", "POST"):
            if self.overloaded:
                return httpx.Response(503, text="no nodes available to schedule pods")
            if method == "PUT" and body["service"] not in self.functions:
                return httpx.Response(404, text="not found")
            self.functions[body["service"]] = {
                **body,
                "name": body["service"],
                "replicas": 1,
                "availableReplicas": 1,
            }
            return httpx.Response(202)
        return httpx.Response(202 if self.functions.pop(body["functionName"], None) else 404)

    def _function(self, path: str, body: dict[str, Any]) -> httpx.Response:
        name = path.rsplit("/", 1)[-1]
        found = self.functions.get(name)
        if found is None:
            return httpx.Response(404, text="not found")
        if path.startswith("/system/function/"):
            return httpx.Response(200, json=found)
        found["replicas"] = found["availableReplicas"] = body["replicas"]
        return httpx.Response(202)


def _driver(gateway: FakeGateway) -> OpenFaasDriver:
    client = httpx.Client(base_url=_CFG.gateway_url, transport=httpx.MockTransport(gateway))
    return OpenFaasDriver("faas", _CFG, http=client)


def test_openfaas_driver_conforms() -> None:
    assert_driver_conformance(_driver(FakeGateway()))


def test_scheduling_failure_is_provision_refused_and_not_retried() -> None:
    gateway = FakeGateway(overloaded=True)
    with pytest.raises(ProvisionRefused):
        _driver(gateway).up(_SPEC)
    deploys = [r for r in gateway.requests if r.url.path == "/system/functions"]
    assert len(deploys) == 1


def test_freeze_and_stop_both_scale_to_zero_but_read_differently() -> None:
    gateway = FakeGateway()
    driver = _driver(gateway)
    handle = driver.up(_SPEC)
    driver.stop(handle)
    assert driver.status(handle) is RuntimeState.STOPPED
    driver.freeze(handle)
    assert driver.status(handle) is RuntimeState.FROZEN
    assert driver.thaw(handle) == handle
    function = gateway.functions[handle.ref]
    assert function["envVars"][RESUME_ENV] == "1"
    assert driver.status(handle) is RuntimeState.RUNNING


def test_unreachable_gateway_reads_as_unknown() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(base_url=_CFG.gateway_url, transport=httpx.MockTransport(refuse))
    driver = OpenFaasDriver("faas", _CFG, http=client)
    assert driver.status(RemoteHandle("faas", "grove-x")) is None


def test_emitted_stack_pins_one_replica_and_names_the_secret() -> None:
    stack = json.loads(_driver(FakeGateway()).emit_spec(_SPEC))
    function = stack["functions"][f"grove-{_SPEC.workspace_id}"]
    assert function["labels"]["com.openfaas.scale.max"] == "1"
    assert function["secrets"] == ["grove-daemon-token"]
    assert len(f"grove-{_SPEC.workspace_id}") <= 63
