"""Installer-script contract: never resolve the bare PyPI name ``grove``.

The name ``grove`` on PyPI belongs to an unrelated log-collection framework,
so an installer that defaults to ``uv tool install grove`` installs the wrong
product. The public GitHub mirror also publishes only the ``current`` branch
(never ``main``), so every raw URL and git ref the installers embed must
point at refs that exist there. These are text-contract pins, sibling to
``test_systemd_packaging.py``: they break loudly if either regression comes
back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from grove import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = (_REPO_ROOT / "install.sh").read_text(encoding="utf-8")
_INSTALL_PS1 = (_REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
_MAKEFILE = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")


def _bash_is_real() -> bool:
    """True when ``bash`` runs commands (not the Windows WSL-launcher stub)."""
    if shutil.which("bash") is None:
        return False
    try:
        result = subprocess.run(
            ["bash", "-c", "echo grove-shell-probe"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and "grove-shell-probe" in result.stdout


@pytest.mark.skipif(not _bash_is_real(), reason="no real bash available")
def test_install_sh_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(_REPO_ROOT / "install.sh")], check=True, timeout=10)


def test_install_sh_defaults_to_git_source() -> None:
    assert "git+https://github.com/${REPO}@${REF}" in _INSTALL_SH
    # A bare-name default would resolve from PyPI to the unrelated package.
    assert 'SOURCE="grove"' not in _INSTALL_SH
    assert "--stable" not in _INSTALL_SH


def test_install_sh_verification_is_not_masked() -> None:
    assert "grove version 2>/dev/null || true" not in _INSTALL_SH
    assert "grove version" in _INSTALL_SH


def test_install_ps1_defaults_to_git_source() -> None:
    assert "git+https://github.com/$Repo@$Ref" in _INSTALL_PS1
    assert "$Source = 'grove'" not in _INSTALL_PS1
    assert "'canary'" not in _INSTALL_PS1


def test_no_reference_to_dead_main_ref_on_the_public_mirror() -> None:
    for name, text in (
        ("install.sh", _INSTALL_SH),
        ("install.ps1", _INSTALL_PS1),
        ("Makefile", _MAKEFILE),
    ):
        assert "bearlike/Grove/main/" not in text, f"{name} references the dead /main/ raw URL"
        assert "@main" not in text, f"{name} references the dead @main git ref"


def test_declared_versions_agree() -> None:
    """``grove.__version__`` is a hand-written literal, so nothing makes it
    follow a ``pyproject.toml`` bump — and every surface that reports a version
    (the CLI, the OTel resource, the release check) reads the literal while the
    wheel carries the metadata. They drifted to 0.0.9/0.0.10 exactly this way.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]
    assert __version__ == declared, (
        f"grove.__version__ is {__version__} but pyproject.toml declares {declared}"
    )

    def _json(*parts: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(_REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8"))
        return loaded

    # The plugin manifests are the ones that drifted furthest (1.4.0 / 1.0.0
    # against a 0.0.10 package) because this test once stopped at the webapp:
    # every artifact the stack ships carries the one release version.
    lock = _json("webapp", "package-lock.json")
    marketplace = _json(".claude-plugin", "marketplace.json")
    manifests = {
        "webapp/package.json": _json("webapp", "package.json")["version"],
        "webapp/package-lock.json": lock["version"],
        "webapp/package-lock.json root package": lock["packages"][""]["version"],
        "src/grove/.claude-plugin/plugin.json": _json(
            "src", "grove", ".claude-plugin", "plugin.json"
        )["version"],
        ".claude-plugin/marketplace.json": marketplace["version"],
        ".claude-plugin/marketplace.json plugin entry": marketplace["plugins"][0]["version"],
    }
    drifted = {name: found for name, found in manifests.items() if found != declared}
    assert not drifted, f"pyproject.toml declares {declared}, but {drifted}"


def test_distribution_name_is_not_the_taken_pypi_name() -> None:
    """The distribution is ``grove-factory``; ``grove`` on PyPI is someone else's
    package. Self-referential extras must track the distribution name or
    ``.[all]`` resolves nothing.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "grove-factory"
    for extra, requirements in pyproject["project"]["optional-dependencies"].items():
        for requirement in requirements:
            assert not requirement.startswith("grove["), (
                f"extra {extra!r} self-references the taken name: {requirement}"
            )
