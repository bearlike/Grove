"""An egress allowlist entry that resolves to nothing must say so.

`allow` is loud about a resolver answering `0.0.0.0` — the Pi-hole/AdGuard case
that turns an allow into a rule that is not one. A name that resolves to NO
address never reaches `allow` at all, so it was dropped from the firewall in
silence on every container start. Measured live: `host.docker.internal` returns
zero IPv4 addresses in a plain bridge-network container, so a Grove-plane entry
was vanishing with nothing said.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_policy import EgressPolicy


def _policy() -> EgressPolicy:
    return EgressPolicy.derive(GroveConfig().container.egress, kind="claude_code", remote_urls=())


def test_the_resolution_loop_reports_a_host_that_resolves_to_nothing() -> None:
    script = _policy().firewall_script()
    assert "grove: egress allowlist skipping" in script
    # The `grove:` marker is what `_ProvisionLog` selects out of the container's
    # stderr, so this reaches the provision log AND any raised diagnosis.
    assert script.count('[ -n "$addrs" ]') == 1


def test_the_generated_script_is_valid_posix_shell(tmp_path: Path) -> None:
    """The loop was rewritten from a `for` over a substitution to a variable
    plus a guard; a quoting slip there fails the whole container start under
    `set -e`, which is the most expensive way to find out."""
    sh = shutil.which("sh")
    if sh is None:  # pragma: no cover - every CI image has one
        pytest.skip("no POSIX shell available")
    path = tmp_path / "egress.sh"
    path.write_text(_policy().firewall_script(), encoding="utf-8")
    assert subprocess.run([sh, "-n", str(path)], capture_output=True, check=False).returncode == 0


def test_an_unresolvable_host_still_emits_no_firewall_rule() -> None:
    """The warning is additive. The invariant it must not break is the one the
    section already states: never emit a rule for an unusable address."""
    script = _policy().firewall_script()
    loop = script[script.index("for host in") :]
    guard = loop.index('[ -n "$addrs" ]')
    assert loop.index("for addr in $addrs", guard) > guard
