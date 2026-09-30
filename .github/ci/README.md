# CI component map

Which paths belong to which CI component, and therefore which jobs a change runs.

```
ci/
├── components.json   the map: each component's sources, tests and dependents
└── affected.py       changed paths in → `component=true|false` lines out
```

- **`components.json`** is the single place to edit. A new package or test
  directory gets an owner here, never a path list in a workflow.
- **`affected.py`** selects a component when a changed path starts with one of
  its `sources` or `tests`. It then selects that component's `dependents`,
  transitively, because they import it: a `core` change runs every Python
  component.
- **It fails open.** A path nobody owns, or a change to `always_run_on`
  (`.github/`, `pyproject.toml`, `uv.lock`, `Makefile`), runs every component.
  A filter that skips tests on a mistake would report green for unchecked work.

`tests/test_ci_components.py` keeps the map honest. Every source package and
every test file must have exactly one owner, and some `ci.yml` job must actually
run every test file.

```bash
git diff --name-only origin/main | python3 .github/ci/affected.py
```
