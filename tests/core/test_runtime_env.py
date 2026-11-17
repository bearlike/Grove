"""A runtime block's ``*_env`` variable is refused by name before any I/O (#1049).

Written from the incident: with ``OPEN_SANDBOX_URL`` unset, ``grove create
--runtime sandbox`` failed with httpx's "Request URL is missing an 'http://' or
'https://' protocol", which names no variable, because an unset ``url_env`` read
as ``""`` and became a relative base URL.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from grove.core.errors import ConfigError
from grove.core.runtimes import WorkspaceSpec
from grove.core.runtimes._cli import CliResult, ProviderCli
from grove.core.runtimes._env import RuntimeEnv
from grove.core.runtimes.lambda_microvm import LambdaMicrovmDriver
from grove.core.runtimes.openfaas import OpenFaasDriver
from grove.core.runtimes.opensandbox import OpenSandboxDriver
from tests.core.test_runtimes_contract import _SPEC
from tests.core.test_runtimes_lambda_microvm import _CFG as _MICROVM
from tests.core.test_runtimes_openfaas import _CFG as _FAAS
from tests.core.test_runtimes_opensandbox import _CFG as _SANDBOX


def test_an_unset_variable_is_refused_naming_it_and_its_dotted_path() -> None:
    env = RuntimeEnv("sbx", environ={})
    with pytest.raises(ConfigError) as refused:
        env.require("url_env", "OPEN_SANDBOX_URL")
    assert "runtimes.sbx.url_env" in str(refused.value)
    assert "OPEN_SANDBOX_URL" in str(refused.value)


def test_an_empty_value_is_refused_like_an_unset_one() -> None:
    with pytest.raises(ConfigError, match="OPEN_SANDBOX_URL"):
        RuntimeEnv("sbx", environ={"OPEN_SANDBOX_URL": ""}).require("url_env", "OPEN_SANDBOX_URL")


def test_a_set_variable_is_returned() -> None:
    env = RuntimeEnv("sbx", environ={"OPEN_SANDBOX_URL": "https://sandbox.example.test"})
    assert env.require("url_env", "OPEN_SANDBOX_URL") == "https://sandbox.example.test"


def test_a_required_field_left_empty_is_refused_by_its_path() -> None:
    with pytest.raises(ConfigError, match=r"runtimes\.sbx\.url_env is empty"):
        RuntimeEnv("sbx", environ={}).require("url_env", "")


def test_an_optional_field_left_empty_is_off_and_reads_nothing() -> None:
    class Untouchable(dict[str, str]):
        def get(self, *_: Any) -> str:  # type: ignore[override]
            raise AssertionError("an empty field must not read the environment")

    assert RuntimeEnv("sbx", environ=Untouchable()).optional("registry_user_env", "") == ""


def test_an_optional_field_naming_an_unset_variable_is_refused() -> None:
    """Naming the variable is the opt-in, so a missing value is a mistake, not "off"."""
    with pytest.raises(ConfigError, match=r"runtimes\.sbx\.registry_user_env names REG_USER"):
        RuntimeEnv("sbx", environ={}).optional("registry_user_env", "REG_USER")


# ─── each driver refuses before it opens a connection ────────────────────────


def _no_io(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request may be made: {request.url}")


def test_opensandbox_refuses_an_unset_url_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident: the driver builds its client from ``url_env`` at construction."""
    monkeypatch.delenv("OPEN_SANDBOX_URL", raising=False)
    monkeypatch.setenv("OPEN_SANDBOX_API_KEY", "k")
    with pytest.raises(ConfigError, match=r"runtimes\.sbx\.url_env names OPEN_SANDBOX_URL"):
        OpenSandboxDriver("sbx", _SANDBOX)


def test_opensandbox_refuses_an_unset_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SANDBOX_URL", "https://sandbox.example.test")
    monkeypatch.delenv("OPEN_SANDBOX_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPEN_SANDBOX_API_KEY"):
        OpenSandboxDriver("sbx", _SANDBOX)


def test_opensandbox_refuses_half_an_image_credential_before_the_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private image's user is named and set, its password is named and not set."""
    monkeypatch.setenv("SBX_REGISTRY_USER", "robot")
    monkeypatch.delenv("SBX_REGISTRY_PASSWORD", raising=False)
    cfg = _SANDBOX
    client = httpx.Client(
        base_url="https://sandbox.example.test", transport=httpx.MockTransport(_no_io)
    )
    driver = OpenSandboxDriver("sbx", cfg, http=client)
    spec = WorkspaceSpec(
        workspace_id=_SPEC.workspace_id,
        image=_SPEC.image,
        command=_SPEC.command,
        env=_SPEC.env,
        secret_env={"GROVE_WORKER_TOKEN": "t"},
    )
    refusal = r"runtimes\.sbx\.image_auth_password_env names SBX_REGISTRY_PASSWORD"
    with pytest.raises(ConfigError, match=refusal):
        driver.up(spec)


def test_openfaas_refuses_an_unset_password_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENFAAS_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match=r"runtimes\.faas\.password_env names OPENFAAS_PASSWORD"):
        OpenFaasDriver("faas", _FAAS)


class _NoCli(ProviderCli):
    def call(self, argv: Any, **_: Any) -> CliResult | None:  # type: ignore[override]
        raise AssertionError(f"no command may run: {argv}")


def test_lambda_microvm_refuses_an_unset_token_arn_before_invoking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROVE_DAEMON_TOKEN_ARN", raising=False)
    driver = LambdaMicrovmDriver("mvm", _MICROVM, cli=_NoCli(timeout_seconds=1))
    with pytest.raises(ConfigError, match="GROVE_DAEMON_TOKEN_ARN"):
        driver.up(_SPEC)
