"""The optional-SDK import seam must tell absent apart from incompatible.

The incident these pin: SDK 2.0.0 removed ``mcp.server.fastmcp``, and a bare
``except ImportError`` around that deep submodule import told the operator to
install a package that was already installed — so the version, the actual
cause, never came up. Every assertion here is about the MESSAGE a human reads.
"""

from __future__ import annotations

import pytest

from grove._mcp_sdk import McpSdk

pytest.importorskip("mcp", reason="the [mcp] extra is what these diagnostics describe")


def test_absent_distribution_reports_the_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(McpSdk, "DISTRIBUTION", "grove_absent_distribution")
    sdk = McpSdk("grove-mcp")

    with pytest.raises(ImportError) as excinfo:
        sdk.load("grove_absent_distribution.server", "Thing")

    assert str(excinfo.value) == (
        "grove-mcp requires the MCP SDK — install with: pip install 'grove[mcp]'"
    )
    assert excinfo.value.__cause__ is not None


def test_missing_submodule_names_version_range_and_cause() -> None:
    """The 2.0.0 shape: the distribution is present, the submodule is gone."""
    sdk = McpSdk("grove-mcp")

    with pytest.raises(ImportError) as excinfo:
        sdk.load("mcp.grove_no_such_submodule", "Thing")

    message = str(excinfo.value)
    installed = McpSdk.installed_version()
    assert installed is not None
    assert installed in message
    assert ">=1.2,<2" in message
    assert "grove_no_such_submodule" in message, "the underlying import error must survive"
    assert "grove-mcp" in message
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_missing_symbol_is_diagnosed_like_a_missing_submodule() -> None:
    """A symbol that moved is the same failure as a module that moved."""
    sdk = McpSdk("grove channel server")

    with pytest.raises(ImportError) as excinfo:
        sdk.load("mcp", "GroveNoSuchSymbol")

    message = str(excinfo.value)
    assert "grove channel server" in message
    assert ">=1.2,<2" in message
    assert isinstance(excinfo.value.__cause__, AttributeError)


def test_importable_without_metadata_is_installed_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``dist-info`` does not mean absent — a checkout on ``sys.path`` has none."""
    monkeypatch.setattr(McpSdk, "DISTRIBUTION", "json")

    assert McpSdk.installed_version() is None
    assert McpSdk.is_installed() is True
    # The version is unknown, but the failure is still reported as incompatible
    # (it names the supported range) rather than as an absent distribution.
    assert ">=1.2,<2" in McpSdk("grove-mcp").diagnose(ImportError("boom"))
