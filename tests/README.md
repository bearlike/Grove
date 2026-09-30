# tests

The pytest suite. Each directory mirrors the package it tests and runs in that
component's CI job.

```
tests/
├── core/              engine tests                    → CI: core, core subsystems
│   └── agents/ usage/ tickets/ issueops/ contracts/     (the "core subsystems" job)
├── daemon/            FastAPI routes and SSE           → CI: daemon
├── tui/               Textual Pilot tests              → CI: tui + cli
├── cli/               `grove` command tests            → CI: tui + cli
├── client/            client SDK                       → CI: client + mcp + tooling
├── mcp/               MCP server                       → CI: client + mcp + tooling
├── tools/             screenshot tooling, skills, docs helpers → CI: client + mcp + tooling
├── docs/              docs site checks                 → CI: docs build
├── integration/       real tmux + real git (`-m integration`) → CI: integration
├── test_ci_components.py   the CI component map itself → CI: workflows lint
├── test_*.py          cross-cutting contracts (install, packaging, statusline…) → CI: core
└── conftest.py        shared fixtures and the autouse isolation guards
```

```bash
uv run pytest                      # unit suite (what most jobs run)
uv run pytest -m integration       # real tmux/git — never from inside a tmux pane without `env -u TMUX`
uv run pytest tests/daemon -k remap
```

A new test directory needs an owner in
[`.github/ci/components.json`](../.github/ci/components.json) and a job in
`ci.yml` that runs it, or `test_ci_components.py` fails. Conventions and the
CI/lint traps are in [CLAUDE.md](CLAUDE.md).
