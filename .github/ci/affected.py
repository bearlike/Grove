"""Decide which CI components a set of changed paths affects.

Reads `components.json` beside this file. A path selects the component whose
`sources` or `tests` prefix it starts with; a selected component also selects
its `dependents`, transitively, because they import it. Stdlib only, so the
`changes` job can run it before any dependency is installed.

FAILS OPEN. A path no component owns, or a path under `always_run_on`, selects
everything: a gate whose failure mode is "skip the tests" reports green for
work nothing checked. The ownership test (tests/test_ci_components.py) is what
keeps "no owner" rare, so falling open costs a slow run, never a missed one.

Usage: `affected.py < changed-paths.txt` prints one `name=true|false` line per
component, the format `$GITHUB_OUTPUT` takes. `affected.py --all` selects every
component without reading stdin.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

MAP_PATH = Path(__file__).with_name("components.json")


@dataclass(frozen=True, slots=True)
class ComponentMap:
    owners: dict[str, tuple[str, ...]]
    dependents: dict[str, tuple[str, ...]]
    always_run_on: tuple[str, ...]

    @classmethod
    def load(cls, path: Path = MAP_PATH) -> ComponentMap:
        raw = json.loads(path.read_text(encoding="utf-8"))
        components = raw["components"]
        return cls(
            owners={
                name: tuple(spec["sources"]) + tuple(spec["tests"])
                for name, spec in components.items()
            },
            dependents={name: tuple(spec["dependents"]) for name, spec in components.items()},
            always_run_on=tuple(raw["always_run_on"]),
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.owners)

    def owner_of(self, path: str) -> str | None:
        """The component owning `path`, by longest matching prefix."""
        best: tuple[int, str] | None = None
        for name, prefixes in self.owners.items():
            for prefix in prefixes:
                matches = path == prefix or path.startswith(prefix)
                if matches and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), name)
        return best[1] if best else None

    def affected(self, paths: list[str]) -> set[str]:
        selected: set[str] = set()
        for path in paths:
            if any(path.startswith(p) for p in self.always_run_on):
                return set(self.names)
            owner = self.owner_of(path)
            if owner is None:
                return set(self.names)
            selected.add(owner)
        frontier = list(selected)
        while frontier:
            for dependent in self.dependents[frontier.pop()]:
                if dependent not in selected:
                    selected.add(dependent)
                    frontier.append(dependent)
        return selected


def main() -> None:
    component_map = ComponentMap.load()
    if "--all" in sys.argv[1:]:
        chosen = set(component_map.names)
    else:
        paths = [line.strip() for line in sys.stdin if line.strip()]
        chosen = component_map.affected(paths)
    for name in component_map.names:
        print(f"{name}={'true' if name in chosen else 'false'}")


if __name__ == "__main__":
    main()
