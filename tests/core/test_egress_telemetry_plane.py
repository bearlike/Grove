"""The telemetry plane: an enabled OTLP endpoint must survive the firewall.

Containers are default-on and default to `mode: "allowlist"`, so before this the
allowlist unioned the agent plane, the package plane, the repo's git remotes and
the Grove plane — and nothing at all for the endpoint Grove points the agent's
exporter at. Every span from a containerized workspace was dropped at the
firewall with no diagnostic anywhere.

The compounding hazard is the direction the egress config fails in: anything but
`open` fails CLOSED, so a fix that turns a bad telemetry host into an
inapplicable firewall trades silent telemetry loss for a workspace that does not
start. That trade is refused here — telemetry is a convenience, and every test
below that feeds the derivation something unusable asserts the firewall still
applies.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from grove.core.config import EgressConfig, TelemetryConfig
from grove.core.container_policy import EgressPolicy

#: A self-hosted deployment is the NORMAL configuration for this feature, not an
#: exception — a LAN name on a private TLD, resolvable only from inside that
#: network, and frequently not from inside a bridge-network container.
SELF_HOSTED = {
    "LANGFUSE_HOST": "https://langfuse.acme.home",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
    "LANGFUSE_SECRET_KEY": "sk-lf-test",
}


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


# ─── deriving the plane ─────────────────────────────────────────────────────


def test_a_self_hosted_endpoint_joins_the_allowlist() -> None:
    """The `.home` LAN case, which is the deployment this exists for."""
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env=SELF_HOSTED,
    )
    assert "langfuse.acme.home" in policy.hosts
    # The endpoint's PATH is not a destination, and neither is its scheme.
    assert not any(entry.startswith("https://") for entry in policy.hosts)


def test_an_rfc1918_endpoint_becomes_an_address_rule() -> None:
    """A private-address endpoint is the other half of self-hosting, and it must
    reach the firewall as a literal rather than as a name for `getent`."""
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env={**SELF_HOSTED, "LANGFUSE_HOST": "http://192.168.10.4:3000"},
    )
    assert "192.168.10.4" in policy.cidrs
    assert 'allow "192.168.10.4"' in policy.firewall_script()


def test_disabled_telemetry_contributes_nothing() -> None:
    baseline = EgressPolicy.derive(EgressConfig(), kind="claude_code", fetch_ranges=lambda _s: ())
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(),
        env=SELF_HOSTED,
        fetch_ranges=lambda _s: (),
    )
    assert policy.hosts == baseline.hosts


def test_a_partial_credential_set_still_allows_the_host() -> None:
    """`derive_env` emits the OTLP endpoint only when all three values resolve,
    but the operator's intent about WHERE is already unambiguous from the host
    alone — and a firewall that admits a host nothing exports to costs nothing,
    while one that blocks it is the defect this closes."""
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env={"LANGFUSE_HOST": "https://langfuse.acme.home"},
    )
    assert "langfuse.acme.home" in policy.hosts


def test_a_host_variable_without_a_scheme_is_reported(warnings_logged: list[str]) -> None:
    """Unparseable as a URL, and equally unusable to the exporter — the firewall
    is not the layer that failed, but it is the layer that notices."""
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env={**SELF_HOSTED, "LANGFUSE_HOST": "langfuse.acme.home:3000"},
    )
    assert "langfuse.acme.home" not in policy.hosts
    assert any("names no host" in message for message in warnings_logged)


# ─── the failure that must stay visible ─────────────────────────────────────


def test_deny_mode_says_telemetry_cannot_get_out(warnings_logged: list[str]) -> None:
    """`deny` permits loopback and the workspace network only, and telemetry is
    by definition off-box. The combination is legitimate and unreconcilable, so
    it is announced rather than discovered later as an empty trace tree."""
    policy = EgressPolicy.derive(
        EgressConfig(mode="deny"),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env=SELF_HOSTED,
    )
    assert policy.mode == "deny"
    assert any(
        "langfuse.acme.home" in message and "dropped at the firewall" in message
        for message in warnings_logged
    )


def test_open_mode_reaches_the_endpoint_without_a_word(warnings_logged: list[str]) -> None:
    """`open` is the supported, un-nagged no-firewall path — nothing is blocked,
    so there is nothing to report."""
    EgressPolicy.derive(
        EgressConfig(mode="open"),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env=SELF_HOSTED,
    )
    assert warnings_logged == []


# ─── never a launch failure ─────────────────────────────────────────────────


def test_an_ipv6_endpoint_is_dropped_rather_than_emitted(warnings_logged: list[str]) -> None:
    """`iptables -d` refuses a v6 address under `set -e`, so one such rule aborts
    the whole ruleset and fails the container start. A misconfigured destination
    must never cost the workspace."""
    policy = EgressPolicy.derive(
        EgressConfig(mode="allowlist", allow=("fd00:1234::9/64",)),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env={**SELF_HOSTED, "LANGFUSE_HOST": "http://[fd00:1234::9]:3000"},
        fetch_ranges=lambda _s: (),
    )
    assert not any(":" in entry for entry in (*policy.hosts, *policy.cidrs))
    assert "fd00:1234::9" not in policy.firewall_script()
    assert sum("IPv4-only" in message for message in warnings_logged) == 2


def test_an_unresolvable_telemetry_host_leaves_the_firewall_standing(tmp_path: Path) -> None:
    """The whole fail-closed trap, executed rather than read.

    A `.home` endpoint routinely resolves to nothing from inside a bridge-network
    container, and the resolution loop runs under `set -euo pipefail` where
    `getent`'s exit 2 for such a name would abort the script — taking every rule
    after it, and with it the container start. Adding a name that is MORE likely
    than any other entry to be unresolvable is only safe while that holds, so it
    is asserted against the real shell, on the real generated text.
    """
    sh = shutil.which("bash")
    if sh is None:  # pragma: no cover - CI runs Linux
        pytest.skip("no bash available")
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        telemetry=TelemetryConfig(enabled=True),
        env=SELF_HOSTED,
        fetch_ranges=lambda _s: (),
    )
    script = policy.firewall_script()
    start = script.index("for host in")
    end = script.index("\ndone", script.index("for addr in", start)) + len("\ndone")
    driver = tmp_path / "loop.sh"
    driver.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                'allow() { echo "rule $1"; }',
                # getent's real contract: exit 2 when no database entry matches,
                # which is what a private-TLD name gets inside a container.
                "getent() { return 2; }",
                script[start:end],
                'echo "loop survived"',
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run([sh, str(driver)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "loop survived" in result.stdout
    assert "grove: egress allowlist skipping langfuse.acme.home" in result.stderr
