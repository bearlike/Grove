"""The CI component map must own every package and every test exactly once.

`.github/ci/components.json` decides which jobs a change runs. A package with no
owner falls open (runs everything) — slow but safe. A TEST FILE claimed by two
components runs twice, and one claimed by none never runs in its component's
job at all — silent, and green. That second case is the one this file exists
for, because nothing else would ever notice it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_affected", _ROOT / ".github/ci/affected.py")
assert _spec is not None and _spec.loader is not None
affected = importlib.util.module_from_spec(_spec)
sys.modules["ci_affected"] = affected
_spec.loader.exec_module(affected)

MAP = affected.ComponentMap.load()


def _test_files() -> list[str]:
    return sorted(
        p.relative_to(_ROOT).as_posix()
        for p in (_ROOT / "tests").rglob("test_*.py")
        if "__pycache__" not in p.parts
    )


def test_every_test_file_has_exactly_one_owner() -> None:
    claims: dict[str, list[str]] = {}
    for path in _test_files():
        claims[path] = [
            name
            for name, prefixes in MAP.owners.items()
            if any(path == p or path.startswith(p) for p in prefixes)
        ]
    unowned = [p for p, owners in claims.items() if not owners]
    assert not unowned, f"no CI component runs these tests: {unowned}"
    doubled = {p: owners for p, owners in claims.items() if len(owners) > 1}
    assert not doubled, f"these tests would run in several jobs: {doubled}"


def _collected(*args: str) -> set[str]:
    """The node ids pytest itself selects for these arguments.

    Asking pytest rather than re-implementing its selection is the point: a
    path match cannot see a marker or a `-k` expression, and both deselect.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")},
    )
    assert result.returncode in (0, 5), result.stdout[-2000:] + result.stderr[-2000:]
    return {line for line in result.stdout.splitlines() if "::" in line}


def _job_args(inputs: dict[str, object]) -> list[str]:
    """The pytest argv `_py-tests.yml` builds from a caller's inputs, defaults included."""
    args = str(inputs.get("paths", "tests")).split()
    for ignored in str(inputs.get("ignore", "")).split():
        args += ["--ignore", ignored]
    markers = str(inputs.get("markers", "not integration"))
    if markers:
        args += ["-m", markers]
    keyword = str(inputs.get("keyword", ""))
    if keyword:
        args += ["-k", keyword]
    return args


def _selected_by(workflow: str) -> set[str]:
    doc = yaml.safe_load((_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    selected: set[str] = set()
    for job in doc["jobs"].values():
        uses = str(job.get("uses", ""))
        if uses.endswith("_py-tests.yml"):
            selected |= _collected(*_job_args(job.get("with") or {}))
        elif uses.endswith("_docs-build.yml"):
            selected |= _collected("tests/docs", "-m", "not integration")
        elif uses.endswith("_workflows-lint.yml"):
            selected |= _collected("tests/test_ci_components.py")
    return selected


@pytest.fixture(scope="module")
def every_test() -> set[str]:
    return _collected("tests", "-m", "integration or not integration")


def test_every_test_is_selected_by_a_ci_job(every_test: set[str]) -> None:
    """Ownership is not execution, and a path is not a selection.

    `tests/docs` was owned by a component with no pytest job and never ran. A
    test marked `integration` inside a directory whose job runs `not
    integration` is deselected there, and the integration job only covers the
    components it is gated on. Both passed a check that matched paths; this
    asks pytest what each job actually selects. The soak test is the one
    deliberate exception, and the next test holds nightly to it.
    """
    soak = "test_idle_activity_soak"
    unselected = sorted(n for n in every_test - _selected_by("ci.yml") if soak not in n)
    assert not unselected, f"no ci.yml job selects: {unselected[:20]}"


def test_marker_selected_jobs_run_for_every_python_change() -> None:
    """Selection is not execution either: a job must also RUN when its tests change.

    A job selecting by marker across all of `tests` reaches every component's
    directory, so gating it on a subset of components means a change to any
    other one leaves its integration tests selected and never run.
    """
    ci = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    for name, job in ci["jobs"].items():
        inputs = job.get("with") or {}
        if str(job.get("uses", "")).endswith("_py-tests.yml") and inputs.get("markers"):
            assert job.get("if") == "needs.changes.outputs.python == 'true'", (
                f"{name} selects by marker across every component "
                f"but runs only when {job.get('if')}"
            )


def test_nightly_selects_every_test(every_test: set[str]) -> None:
    """ci.yml deselects the soak test, so nightly.yml must select it — and everything.

    Nightly is the net for a dependency the component map misses, so a test it
    does not select is a test no run ever reaches.
    """
    unselected = sorted(every_test - _selected_by("nightly.yml"))
    assert not unselected, f"nightly.yml selects none of: {unselected[:20]}"


def test_no_job_installs_uv_into_the_shared_tool_cache() -> None:
    """setup-uv writes the runners' ONE shared tool-cache volume unless told otherwise.

    Two jobs doing that at once corrupt it — that race is why this CI used to be
    a chain. Every job goes through .github/actions/setup-python-env, which
    points RUNNER_TOOL_CACHE at the job's own temp dir. A direct `setup-uv`
    step must do the same, or it silently brings the race back.
    """
    offenders = []
    for path in sorted((_ROOT / ".github").rglob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps = [s for job in (doc.get("jobs") or {}).values() for s in job.get("steps") or []]
        steps += (doc.get("runs") or {}).get("steps") or []
        for step in steps:
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv"):
                env = step.get("env") or {}
                if "runner.temp" not in str(env.get("RUNNER_TOOL_CACHE", "")):
                    offenders.append(path.relative_to(_ROOT).as_posix())
    assert not offenders, f"setup-uv on the shared tool cache in: {offenders}"


@pytest.mark.parametrize(
    "folder",
    [
        # Top-level folders: each README maps the hierarchy beneath it.
        "src",
        "tests",
        "tools",
        "packaging",
        "docs",
        "webapp",
        # The CI tree. Deliberately NOT `.github/` itself: GitHub renders a
        # `.github/README.md` on the repository front page instead of the root one.
        ".github/workflows",
        ".github/actions",
        ".github/ci",
        "tools/screenshots",
    ],
)
def test_folder_has_a_readme(folder: str) -> None:
    assert (_ROOT / folder / "README.md").is_file(), f"{folder}/ has no README.md"


def test_no_github_dir_readme_shadows_the_front_page() -> None:
    assert not (_ROOT / ".github/README.md").exists(), (
        ".github/README.md replaces the root README on GitHub's repository page"
    )


def test_every_source_package_has_an_owner() -> None:
    # Only what git tracks: `make webapp-bundle` stages gitignored build output
    # under src/grove/_webapp/, which is shipped, not source, and owned by no
    # component.
    ignored = subprocess.run(
        ["git", "check-ignore", *(str(p) for p in (_ROOT / "src/grove").iterdir())],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    packages = sorted(
        p.relative_to(_ROOT).as_posix() + "/"
        for p in (_ROOT / "src/grove").iterdir()
        if p.is_dir() and not p.name.startswith("__") and str(p) not in ignored
    )
    modules = sorted(p.relative_to(_ROOT).as_posix() for p in (_ROOT / "src/grove").glob("*.py"))
    unowned = [p for p in packages + modules if MAP.owner_of(p) is None]
    assert not unowned, f"no CI component owns: {unowned}"


def test_dependents_name_real_components() -> None:
    for name, dependents in MAP.dependents.items():
        unknown = set(dependents) - set(MAP.names)
        assert not unknown, f"{name} names unknown dependents {unknown}"


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        # A leaf change runs only its own component.
        (["src/grove/tui/app.py"], {"tui"}),
        (["webapp/app/page.tsx"], {"webapp"}),
        (["docs/getting-started.md"], {"docs"}),
        # The daemon's OpenAPI is the webapp's contract, so it pulls webapp in.
        (["src/grove/daemon/app.py"], {"daemon", "webapp"}),
        # client is imported by mcp and the tui; mcp is a leaf.
        (["src/grove/client/http.py"], {"client", "mcp", "tui"}),
        # core is imported by everything, transitively.
        (
            ["src/grove/core/manager.py"],
            {"core", "daemon", "tui", "client", "mcp", "tooling", "webapp"},
        ),
        # A test-only change still runs its component.
        (["tests/daemon/test_app.py"], {"daemon", "webapp"}),
    ],
)
def test_affected_follows_ownership_and_dependents(changed: list[str], expected: set[str]) -> None:
    assert MAP.affected(changed) == expected


@pytest.mark.parametrize(
    "changed",
    [
        [".github/workflows/ci.yml"],  # the gates themselves moved
        ["uv.lock"],  # every Python environment moved
        ["some/unowned/file.txt"],  # no owner: must not be skipped
    ],
)
def test_affected_falls_open(changed: list[str]) -> None:
    assert MAP.affected(changed) == set(MAP.names)
