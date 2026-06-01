# Contributing

Bug reports and feature requests on the
[issue tracker](https://github.com/bearlike/Grove/issues). For code, the
round trip looks like the section below.

## Setup

```bash
git clone https://github.com/bearlike/Grove.git
cd Grove
make sync     # uv sync --all-groups (runtime + dev + docs)
make check    # confirm a green baseline before changing anything
```

The full dev environment fits in `.venv` under the project. `uv` manages
it. There is no separate Node toolchain, no Docker, no system services
to start.

## Make targets

Every workflow shells through `make`. CI calls the same targets, so what
works locally works on every push.

| Target | Action |
|---|---|
| `make sync`        | Install runtime, dev, and docs deps via `uv sync --all-groups`. |
| `make lint`        | `ruff check`, `ruff format --check`, `mypy --strict`, `import-linter`. |
| `make format`      | Auto-fix lint and reformat in place. |
| `make type`        | `mypy --strict` only. |
| `make contracts`   | `import-linter` only. Verifies the `core` to `tui` boundary. |
| `make test`        | Unit + Pilot tests (excludes integration). |
| `make integration` | Real-tmux + real-git tests (Linux/macOS only). |
| `make check`       | `make lint && make test`. The standard pre-push gate. |
| `make build`       | Build sdist + wheel into `dist/`. |
| `make uvx-smoke`   | Run `grove version` via `uvx` against the local checkout. |
| `make docs`        | Serve the docs site locally with live reload. |
| `make docs-build`  | Build the docs site (`mkdocs build --strict`). |
| `make help`        | Print every target with its description. |

## Commit format

Gitmoji plus Conventional Commits. The format makes the log scannable.

```
✨ feat(core,tmux): add window-size negotiation on attach
🐛 fix(tui,peek): stop mutating source pane size; clip locally
📝 docs(claude.md): capture rich-side chrome-color pattern
♻️  refactor(tui,keys): hoist footer key partitions to keys.py
🧪 test(tui,footer): pin clay accent, muted separator, dim no color
🎨 style: ruff format pass
```

Scope is comma-separated, no spaces. When a change is engine-only or
client-only, the scope reflects that (`(core,manager)` or `(tui,card)`).
When a change crosses, list both sides.

## PR conventions

- **One purpose per PR.** Bug fixes do not carry surrounding cleanup.
  Refactors do not add features. The exception is a small refactor that
  is required to land the bug fix or feature. Call it out in the PR
  description.
- **Tests pin contracts, not implementation.** New code lands with tests
  that exercise it. A refactor lands without changing tests; the existing
  tests are the contract.
- **`make check` must be green** before you push.
- **Update the right CLAUDE.md.** Engine plus cross-cutting lessons go in
  the repo root. TUI engineering lessons in `src/grove/tui/CLAUDE.md`.
  Visual contract in `docs/design-system.md`. See
  [engineering principles](develop-principles.md) for the routing table.

## Releases

Three install channels:

| Channel | Source | Cadence | Install |
|---|---|---|---|
| **Stable**       | PyPI (`v*` git tags)  | manually tagged | `uvx grove` |
| **Canary**       | git, `current` branch | every push to `current` | `uvx --from git+https://github.com/bearlike/Grove grove` |
| **Pinned commit**| git, specific SHA     | reproducible installs | `uvx --from git+https://github.com/bearlike/Grove@<sha> grove` |

Stable releases publish a wheel and sdist to PyPI via Trusted Publishing
and create a GitHub Release with auto-generated change notes. There are
no per-OS binaries. `uvx` provides the isolation a native binary
would, with less infrastructure.

Canary is git-based by design. Nothing extra to publish. Every commit on
`current` is reachable. Pinning to a SHA gives exact reproducibility
without a parallel pipeline.

## See also

- [Architecture](develop-architecture.md): what the codebase looks like.
- [Engineering principles](develop-principles.md): the rules.
- [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md): the canonical engineering memory.
