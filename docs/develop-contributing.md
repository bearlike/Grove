# Contributing

## Set up and send a change

Bug reports and feature requests go on the
[issue tracker](https://github.com/bearlike/Grove/issues).

## Setup

```bash
git clone https://github.com/bearlike/Grove.git
cd Grove
make sync     # uv sync --all-groups (runtime + dev + docs)
make check    # confirm a green baseline before changing anything
```

The dev environment fits in `.venv`, managed by `uv`. No Node toolchain,
no Docker, no system service.

## Make targets

Every workflow shells through `make`. CI calls the same targets.

| Target | Action |
|---|---|
| `make sync`        | `uv sync --all-groups`: runtime, dev, docs deps. |
| `make lint`        | `ruff check`, `ruff format --check`, `mypy --strict`, `import-linter`. |
| `make format`      | Auto-fix lint, reformat in place. |
| `make type`        | `mypy --strict` only. |
| `make contracts`   | `import-linter` only, the `core`/`tui` boundary. |
| `make test`        | Unit + Pilot tests, excludes integration. |
| `make integration` | Real-tmux + real-git tests, Linux/macOS only. |
| `make check`       | `make lint && make test`, the pre-push gate. |
| `make build`       | Build sdist + wheel into `dist/`. |
| `make uvx-smoke`   | `grove version` via `uvx` against the checkout. |
| `make docs`        | Serve the docs site with live reload. |
| `make docs-build`  | Build the docs site (`mkdocs build --strict`). |
| `make help`        | Print every target and its description. |

## Commit format

Gitmoji plus Conventional Commits keeps the log scannable.

```
✨ feat(core,tmux): add window-size negotiation on attach
🐛 fix(tui,peek): stop mutating source pane size; clip locally
📝 docs(claude.md): capture rich-side chrome-color pattern
♻️  refactor(tui,keys): hoist footer key partitions to keys.py
🧪 test(tui,footer): pin clay accent, muted separator, dim no color
🎨 style: ruff format pass
```

Scope is comma-separated, no spaces, both sides named when a change
crosses (`core,manager` or `tui,card`).

## PR conventions

- **One purpose per PR.** No cleanup riding a bug fix, no features riding a refactor.
- **Tests pin contracts, not implementation.** New code lands with tests. A refactor changes none.
- **`make check` must be green** before you push.
- **Update the right CLAUDE.md.** Root for engine lessons, `src/grove/tui/CLAUDE.md` for TUI, `docs/design-system.md` for the visual contract.

## Releases

Three install channels:

| Channel | Source | Cadence | Install |
|---|---|---|---|
| **Stable**       | PyPI (`v*` git tags)  | manually tagged | `uvx grove` |
| **Canary**       | git, `current` branch | every push to `current` | `uvx --from git+https://github.com/bearlike/Grove grove` |
| **Pinned commit**| git, specific SHA     | reproducible installs | `uvx --from git+https://github.com/bearlike/Grove@<sha> grove` |

Stable publishes a wheel and sdist to PyPI via Trusted Publishing, plus
a GitHub Release. No per-OS binaries: `uvx` gives the isolation a native
binary would, with less infrastructure.

Canary is git-based, so nothing extra to publish, and pinning a SHA
gives reproducibility with no parallel pipeline.

## See also

- [Architecture](develop-architecture.md): what the codebase looks like.
- [Engineering principles](develop-principles.md): the rules.
- [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md): the canonical memory.
