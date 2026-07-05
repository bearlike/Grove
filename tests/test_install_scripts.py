"""Installer-script contract: never resolve the bare PyPI name ``grove`` (#105).

The name ``grove`` on PyPI belongs to an unrelated log-collection framework,
so an installer that defaults to ``uv tool install grove`` installs the wrong
product. The public GitHub mirror also publishes only the ``current`` branch
(never ``main``), so every raw URL and git ref the installers embed must
point at refs that exist there. These are text-contract pins, sibling to
``test_systemd_packaging.py``: they break loudly if either regression comes
back.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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
    # The exact #105 regression: a bare-name default that resolves from PyPI.
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
