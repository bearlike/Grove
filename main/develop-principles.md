# Engineering principles

Grove follows a small set of rules that keep the codebase navigable as it
grows. The full list with examples lives in
[`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md) at
the repo root. The summary below is what every contributor should keep in
mind before opening a PR.

## Ten rules

1. **Modules align with concerns, not technical layers.** Each module
   answers one question, nameable in a single sentence. Boundaries follow
   what changes together, not generic `models / views / controllers`
   buckets.
2. **Public surface is small and explicit.** A package's entry point is
   its contract. Leading underscores on internal modules signal "do not
   import from here." Explicit re-export lists pin what consumers may
   actually depend on.
3. **Deterministic public entry points; subpackages organize by
   concern.** External callers stick to the package root. Internal
   refactors move freely below. Within a package, split by concern
   (`contracts/` for wire-level Pydantic shapes, `git.py` for the git
   side-effect surface, `manager.py` for orchestration). Never split by
   technical layer.
4. **Dependencies flow inward.** Clients import the engine. The engine
   does not import clients. Most circular-import pain traces back to this.
5. **Boring code beats clever code.** Reuse patterns the project already
   establishes. Local cleverness has a price every reader pays.
6. **YAGNI.** Three similar lines is fine. A new helper, class, or
   subpackage costs review surface for years. Pay only when one engineer
   can plausibly own the new boundary.
7. **Pydantic at public-contract boundaries; plain dataclass for
   in-process state.** Pydantic for anything that crosses a client to
   engine boundary now or could in the future. Plain
   `@dataclass(slots=True)` for internal mutable state and intermediate
   IR.
8. **Side effects at the edges, pure logic in the middle.** I/O, network,
   database, time-of-day belong at module boundaries. The decision logic
   in between should be testable without those.
9. **Tests pin contracts, not implementation.** When a test patches a
   private symbol, that path becomes an implicit contract. Moving it
   silently no-ops the patch. Either surface the seam publicly or update
   the test in the same commit.
10. **Comments and docstrings explain WHY, not WHAT.** Names and types
    document the what. Comments carry the constraint, the trade-off, the
    past incident, the surprising invariant.

## Where lessons go

Grove's institutional memory lives in three files. Each has a strict
scope. Keep them separate.

| Concern | File |
|---|---|
| Engine, lifecycle, manager, config cascade, cross-platform, CLI, build, repo-wide policy | [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md) |
| TUI engineering lessons (focus chain, timer cadences, theme module, framework gotchas) | [`src/grove/tui/CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/CLAUDE.md) |
| TUI visual contract (color tokens, layout, typography tiers, component anatomy) | `docs/design-system.md` (internal, not on the public docs site) |

A change that spans both engine and TUI updates both files in the same
commit. A change that is visible *and* introduces an engineering lesson
updates both the visual contract and the engineering doc. The visual
fact and the implementation fact are different concerns even when
shipped together.

Lessons capture the why and the invariant, not line numbers. When code
moves, update the bullet. Do not delete it.

## See also

- [Architecture](develop-architecture.md): what the rules produce.
- [Contributing](develop-contributing.md): the day-to-day workflow.
- [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md): the canonical, expanded list with running session lessons.
