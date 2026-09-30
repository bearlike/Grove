# CI workflows

Every workflow Grove's CI runs. Files starting with `_` are reusable units
(`workflow_call`), each one component's checks. The rest are entry points that
call them.

There is deliberately no `.github/README.md`: GitHub would show it on the
repository's front page instead of the root README.

```
.github/
├── workflows/          ← you are here
│   ├── ci.yml              PRs + pushes to main: component jobs in parallel, one merge check
│   ├── nightly.yml         daily, no path filter: everything + soak test + unlocked install
│   ├── screenshots.yml     push to main + dispatch: regenerate doc images, then publish docs
│   ├── docs.yml            push to main/current + dispatch: publish the site (never workflow_call)
│   ├── dispatch.yml        on-demand console: one gate at any scope (pytest paths, -k, -m)
│   ├── release.yml         published GitHub release (GitHub only): bundle webapp, build, PyPI
│   ├── docs-cleanup.yml    remove a PR's docs preview when it closes
│   ├── issue-ops.yml       issue-comment commands (self-hosted forge)
│   ├── grove-issue-ops.yml reusable issue-ops caller for other repos
│   ├── _py-quality.yml     ruff · format · mypy · import contracts
│   ├── _py-tests.yml       pytest over given paths / markers / -k / ignores
│   ├── _webapp-gate.yml    typecheck · vitest · styling · codegen · registry · ignored
│   ├── _webapp-e2e.yml     ONE Playwright shard (callers fan out one job per shard)
│   ├── _docs-build.yml     mkdocs build --strict + tests/docs, publishes nothing
│   ├── _docs-deploy.yml    build and publish the site (called by docs.yml and screenshots.yml)
│   ├── _docs-screenshots.yml  capture TUI + webapp images, push them back
│   └── _workflows-lint.yml actionlint + the component-map tests
├── actions/            shared setup steps (see actions/README.md)
├── ci/                 which paths belong to which component (see ci/README.md)
└── actionlint.yaml     actionlint config (self-hosted runner labels)
```

## How a pull request is checked

`ci.yml`'s `changes` job decides which components a diff touches, from
[`../ci/components.json`](../ci/components.json). Each touched component's jobs
start at once and none waits on another. A PR needs one check, **`ci-status`**:
it passes when every job succeeded or was skipped as untouched.

| Job | Component | Measured run |
|---|---|---|
| Python quality | any Python | ~1.5 min |
| core, core subsystems | core | ~4.5 / ~1.5 min |
| daemon | daemon | ~4 min |
| tui + cli | tui | ~5 min |
| client + mcp + tooling | client, mcp, tooling | ~1.5 min |
| integration (real tmux/git) | any Python | ~1.5 min |
| webapp checks | webapp | ~3 min |
| e2e 1/4 … 4/4 | webapp | ~8–10 min each |
| docs build | docs | ~2 min |
| workflows lint | workflows | ~1 min |

## Running something on demand

A workflow can only be dispatched once it exists on `main`.

- Everything for some components: dispatch `ci.yml` with `components` (e.g. `daemon webapp`).
- One gate at a precise scope: `dispatch.yml`, e.g.
  `tea actions workflows dispatch dispatch.yml -i suite=python-tests -i paths=tests/daemon -i keyword=remap`.
- Screenshots: dispatch `screenshots.yml` (`dry_run=true` to capture without pushing).
- Everything, unfiltered: dispatch `nightly.yml`.

Design notes and the incidents behind them live in
[`tests/CLAUDE.md`](../../tests/CLAUDE.md#asking-ci-for-less-than-everything).
