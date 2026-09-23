"""A long-lived process can tell when the code on disk has moved past it."""

from __future__ import annotations

import os
from pathlib import Path

from grove.core.loaded_source import LoadedSource


def _tree(root: Path) -> Path:
    (root / "pkg" / "sub").mkdir(parents=True)
    for rel in ("pkg/__init__.py", "pkg/sub/deep.py"):
        path = root / rel
        path.write_text("x = 1\n")
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    return root / "pkg"


def test_an_untouched_tree_is_not_changed(tmp_path: Path) -> None:
    assert LoadedSource.capture(_tree(tmp_path)).changed() is False


def test_an_edit_anywhere_below_the_package_is_seen(tmp_path: Path) -> None:
    # A NESTED file is what a pull rewrites; a flat glob would pass a
    # top-level edit and miss this one.
    package = _tree(tmp_path)
    loaded = LoadedSource.capture(package)

    os.utime(package / "sub" / "deep.py", ns=(2_000_000_000, 2_000_000_000))

    assert loaded.changed() is True


def test_a_file_that_is_not_python_does_not_count(tmp_path: Path) -> None:
    package = _tree(tmp_path)
    loaded = LoadedSource.capture(package)

    (package / "notes.md").write_text("docs are not code")

    assert loaded.changed() is False


def test_a_new_module_counts_as_a_change(tmp_path: Path) -> None:
    package = _tree(tmp_path)
    loaded = LoadedSource.capture(package)

    (package / "added.py").write_text("y = 2\n")

    assert loaded.changed() is True
