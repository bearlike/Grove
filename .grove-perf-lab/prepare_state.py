#!/usr/bin/env python3
"""Derive the lab's mount plan and writable state from the host's own Grove.

Every path here comes from the SAME resolver the daemon uses (`_ClaudeHome`,
`_CodexHome`, `GitRepo.common_dir`, the workspace store) rather than a typed
list. That is the whole point: a hand-written list is how a profile directory
or a linked worktree's common dir gets missed, and the lab then observes less
than the host does while looking like it observed everything.

Two modes, and the difference is what the numbers can prove:

  live    - bind the host's real transcript roots read-only. Faithful, but the
            corpus moves under the measurement, so an idle window is only idle
            if nothing else on the host is working.
  frozen  - copy the corpus into the lab's own tree. Reproducible: an idle
            window is genuinely idle and an append is one the lab made, which
            is what makes a delta attributable.

DELETE A FROZEN ROOT WHEN THE RUN IS DONE. Each one is a full copy of the
host's transcript corpus (4.1 GB here), and they accumulate per run under
`/tmp`. That is not merely untidy: this host's CI runner containers mount a
RAM-backed `/tmp`, so 21 GB of leftover corpora starved them into
`No space left on device` -- 50 failures and 107 errors across a suite that had
nothing to do with the change under test, on a green branch. A measurement
harness that degrades the machine it measures on is measuring itself.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.codex import _CodexHome
from grove.core.agents.transcript_scope import config_dir_scope
from grove.core.config import load_config
from grove.core.git import GitRepo
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import TranscriptContext


def transcript_roots(states) -> list[Path]:
    """Every provider profile root this host's workspaces actually resolve to.

    Scoped per workspace through `config_dir_scope` because a workspace may
    pin its own CLAUDE_CONFIG_DIR/CODEX_HOME - the roots are a function of that
    scope, not a constant, and reading them once outside it silently returns
    the ambient profile for all of them.
    """
    roots: set[Path] = set()
    for state in states:
        variable = TranscriptContext.CONFIG_DIR_ENV.get(state.agent_kind or "")
        override = state.transcript_context.config_dir if state.transcript_context else None
        with config_dir_scope(variable, override):
            if state.agent_kind == "claude_code":
                roots.update(_ClaudeHome.projects_dirs())
            elif state.agent_kind == "codex":
                roots.add(_CodexHome.sessions_dir())
    # The ambient roots too, so a provider with no current workspace is still
    # part of the corpus the daemon would scan on this host.
    roots.update(_ClaudeHome.projects_dirs())
    roots.add(_CodexHome.sessions_dir())
    return sorted(p for p in roots if p.exists())


def collapse(paths) -> list[Path]:
    """Drop a path already covered by an ancestor mounted at the same place."""
    kept: list[Path] = []
    for path in sorted(set(paths), key=lambda p: len(p.parts)):
        if not any(path.is_relative_to(parent) for parent in kept):
            kept.append(path)
    return kept


def main() -> None:
    root = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "frozen"
    for name in ("config", "state", "output", "agent-config", "corpus"):
        (root / name).mkdir(mode=0o700, exist_ok=True)

    store = JsonWorkspaceStore()
    states = store.load_all()

    # Workspace RECORDS only. The store file is the workspace inventory; bearer
    # sessions, hook tokens and share passcodes live in their own files under
    # the same state dir and are deliberately not copied - a lab that could
    # authenticate as the host daemon is not isolated.
    (root / "state" / "state.json").write_text(store.path.read_text())

    resolved = load_config(repo_root=None)
    settings = {
        "projects": sorted({s.repo_root for s in states}),
        # Every outbound and lifecycle surface off: this experiment measures the
        # read/projection backbone. A lab that could notify, pick up issues or
        # export telemetry would both perturb the measurement and act on
        # production the moment it misread a record.
        "container": {"enabled": False},
        "telemetry": {"enabled": False},
        "notifications": {"enabled": False},
        "issueops": {"enabled": False, "pickup_enabled": False},
        "usage": {"quota": {"enabled": False}},
        "builtin_agents": True,
        # Auth OFF, and this is safe ONLY because of the isolation above: the
        # lab publishes no port and sits on a bridge network, so the daemon is
        # reachable from inside the container and nowhere else. The first pass
        # left auth on, sent no bearer, and measured 60 refusals in 0.124s as
        # though they were served requests - a gate that is never passed makes
        # every downstream number a measurement of the gate.
        "auth": {"enabled": False},
        # The one production-shaped knob kept verbatim: the cache budgets ARE
        # what is under measurement, so substituting a default here would
        # measure a configuration nobody runs.
        "transcript_cache": resolved.transcript_cache.model_dump(mode="json"),
    }
    (root / "config" / "config.json").write_text(json.dumps(settings, indent=2))

    repos: set[Path] = set()
    for state in states:
        for path in (Path(state.repo_root), Path(state.worktree_path)):
            if path.exists():
                repos.add(path)
        common = GitRepo(Path(state.repo_root)).common_dir()
        if common is not None and common.exists():
            repos.add(common)

    corpus = transcript_roots(states)
    if mode == "frozen":
        frozen_roots = []
        for source in corpus:
            destination = root / "corpus" / source.relative_to(source.anchor)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # copytree, not a bind: the lab must be able to APPEND to its own
            # copy without the host's writer moving the same files underneath.
            shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
            frozen_roots.append((str(source), str(destination)))
        corpus_mounts: list[Path] = []
    else:
        frozen_roots = []
        corpus_mounts = corpus

    tool_venv = Path.home() / ".local/share/uv/tools/grove"
    interpreter = (tool_venv / "bin/python").resolve()

    manifest = {
        "root": str(root),
        "mode": mode,
        "repo": str(Path.cwd()),
        "uid": os.getuid(),
        "gid": os.getgid(),
        "home": str(Path.home()),
        "workspace_count": len(states),
        "read_only_mounts": [str(p) for p in collapse([*repos, *corpus_mounts])],
        "frozen_corpus": frozen_roots,
        "interpreter": str(interpreter),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # A second copy under the diagnostics mount, because that is the only one
    # of these directories the container can see by a path it knows.
    (root / "output" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "frozen_corpus"}, indent=2))
    if frozen_roots:
        print(f"-- frozen corpus: {len(frozen_roots)} root(s) copied")


if __name__ == "__main__":
    main()
