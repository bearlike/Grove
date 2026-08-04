# Engineering principles

## Rules the codebase follows

Grove follows a small set of rules that keep the codebase navigable as it
grows. The full list with examples lives in
[`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md).

## Ten rules

1. **Modules align with concerns, not technical layers.** Each module answers one question nameable in a sentence.
2. **Public surface is small and explicit.** A leading underscore means do not import from here.
3. **Deterministic public entry points, subpackages organized by concern.** Callers stick to the package root, and internal code moves freely below, split by concern.
4. **Dependencies flow inward.** Clients import the engine, never the reverse.
5. **Boring code beats clever code.** Reuse the project's own patterns rather than pay for local cleverness.
6. **YAGNI.** A new helper, class, or subpackage costs review surface for years, so pay only when it earns an owner.
7. **Pydantic at public-contract boundaries, plain dataclass for in-process state.** Pydantic crosses a client-to-engine boundary, `@dataclass(slots=True)` stays internal.
8. **Side effects at the edges, pure logic in the middle.** I/O, network, and time-of-day stay at module boundaries so the logic between them stays testable.
9. **Tests pin contracts, not implementation.** A test patching a private symbol makes that path an implicit contract, so moving it needs a public seam or an updated test in the same commit.
10. **Comments and docstrings explain WHY, not WHAT.** Names and types cover the what, comments carry the constraint and the trade-off.

## Where lessons go

Grove's institutional memory lives in three files, each with a strict scope.

| Concern | File |
|---|---|
| Engine, lifecycle, config cascade, cross-platform, CLI, build, repo-wide policy | [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md) |
| TUI engineering lessons (focus chain, timers, theme module, framework gotchas) | [`src/grove/tui/CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/CLAUDE.md) |
| TUI visual contract (colors, layout, typography, component anatomy) | `docs/design-system.md` (internal, not on the public docs site) |

A change spanning engine and TUI updates both files in one commit. A
visible change with an engineering lesson updates both docs: the visual
fact and the implementation fact differ even when shipped together.

Lessons capture the why and the invariant, not line numbers. When code
moves, update the bullet, never delete it.

## See also

- [Architecture](develop-architecture.md): what the rules produce.
- [Contributing](develop-contributing.md): the day-to-day workflow.
- [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md): the canonical, expanded list.
