"""Grove's proxy and an existing LLM gateway both claim the same variable.

`ProxyConfig.base_url_env` names the documented gateway seam each runtime
exposes — `ANTHROPIC_BASE_URL` for Claude — which is precisely the variable a
deployment that already fronts its provider traffic exports for itself. Enabling
Grove's proxy used to overwrite it silently, moving every request onto a
different route: an invisible change to where credentials are sent and to how
the account is billed, discoverable only by noticing the bill.

The two are treated as mutually exclusive, resolved in the existing gateway's
favour, and said out loud.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from loguru import logger

from grove.core.config import ProxyConfig

GATEWAY = "http://gateway.internal.home:8317"


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


def test_an_existing_gateway_wins_and_is_announced(warnings_logged: list[str]) -> None:
    cfg = ProxyConfig(enabled=True, host="127.0.0.1", port=9999)
    assert cfg.proxy_env("claude_code", {"ANTHROPIC_BASE_URL": GATEWAY}) == {}
    assert any("ANTHROPIC_BASE_URL" in message for message in warnings_logged)


def test_the_gateway_url_itself_never_reaches_the_log(warnings_logged: list[str]) -> None:
    """A base URL may carry userinfo, so the variable is named and its value is
    not — the operator already knows what they set it to."""
    ProxyConfig(enabled=True).proxy_env("claude_code", {"ANTHROPIC_BASE_URL": GATEWAY})
    assert not any(GATEWAY in message for message in warnings_logged)


def test_only_the_colliding_kind_yields(warnings_logged: list[str]) -> None:
    """Mutual exclusion is per variable, not per config: a deployment fronting
    only Anthropic keeps Grove's proxy for everything else."""
    cfg = ProxyConfig(enabled=True, host="127.0.0.1", port=9999)
    env = {"ANTHROPIC_BASE_URL": GATEWAY}
    assert cfg.proxy_env("codex", env) == {"OPENAI_BASE_URL": "http://127.0.0.1:9999"}


def test_an_empty_inherited_value_is_not_a_gateway(warnings_logged: list[str]) -> None:
    """An exported-but-empty variable declares nothing, and treating it as a
    gateway would disable the proxy for a deployment that never configured one."""
    cfg = ProxyConfig(enabled=True, host="127.0.0.1", port=9999)
    assert cfg.proxy_env("claude_code", {"ANTHROPIC_BASE_URL": "  "}) == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"
    }
    assert warnings_logged == []


def test_grove_own_url_already_exported_is_not_a_collision(warnings_logged: list[str]) -> None:
    """A relaunch inheriting the value Grove itself set last time is the same
    decision, not a competing one."""
    cfg = ProxyConfig(enabled=True, host="127.0.0.1", port=9999)
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"}
    assert cfg.proxy_env("claude_code", env) == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"}
    assert warnings_logged == []


def test_a_disabled_proxy_says_nothing_at_all(warnings_logged: list[str]) -> None:
    assert ProxyConfig().proxy_env("claude_code", {"ANTHROPIC_BASE_URL": GATEWAY}) == {}
    assert warnings_logged == []
