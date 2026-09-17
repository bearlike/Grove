"""Tests for the capture sandbox's root override.

The override is what lets CI move the sandbox off a RAM-backed `/tmp`, so it is
load-bearing for the docs-screenshots workflow rather than a convenience: the
tree holds a year of synthetic transcripts and twelve git repos at once, and
exhausting a memory-capped container kills the job with a status that reads as a
test failure rather than as a full disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tools.screenshots.driver.sandbox import ROOT_ENV, Sandbox


def test_rooted_uses_the_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROOT_ENV, raising=False)

    assert Sandbox.rooted(Path("/tmp/grove-shots")).root == Path("/tmp/grove-shots")


def test_rooted_honours_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROOT_ENV, "/mnt/disk/shots")

    assert Sandbox.rooted(Path("/tmp/grove-shots")).root == Path("/mnt/disk/shots")


# A CI `env:` that resolves to nothing sets the variable to the empty string
# rather than leaving it unset, so blank and whitespace must fall back too —
# otherwise the sandbox silently roots itself at the current directory.
@pytest.mark.parametrize("blank", ["", "   "])
def test_rooted_treats_a_blank_override_as_unset(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv(ROOT_ENV, blank)

    assert Sandbox.rooted(Path("/tmp/grove-shots")).root == Path("/tmp/grove-shots")


def test_activate_redirects_every_lookup_into_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HOME included — a `Path.home()` fallback otherwise reaches the real profile."""
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "sandbox"))
    sandbox = Sandbox.rooted(Path("/tmp/unused"))

    sandbox.activate()

    for name, _child in Sandbox.DIRS:
        assert Path(os.environ[name]).is_relative_to(sandbox.root)
