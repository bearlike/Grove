# Grove — Agent Guide

Grove lets you tend multiple git worktrees like branches in a forest: spin up, switch between, and tear down isolated coding sessions without losing your place. See `README.md` for the product brief.

Maintain context across sessions. When you hit a non-trivial lesson, write it in the **nearest owning `CLAUDE.md`** — the deepest file whose scope it belongs to (see the tree below). This builds distributed working memory, so future sessions run faster and load only what they need.

## Core operating rules

- Build a mental map before proposing fixes. Separate facts from assumptions; keep updating both while investigating.
- Prefer direct evidence (run the code, read the real output) over inference from a description.
- Use absolute dates and timestamps in findings (example: `2026-06-08`).
- Ask the user about ambiguous assumptions instead of guessing silently.
- Do not push to a remote unless explicitly asked.
- Never commit `docs/superpowers/specs/...` — that is session-local working memory; track designs and plans as Gitea issues instead.
- **Never leak host-private details into tracked files.** No absolute home paths, real private repo names, personal profile names, or host/infra specifics in committed docs or code — this repo mirrors to a public remote. Keep examples generic and fictional. Real host and profile configuration lives in your own config (`~/.config/grove/`, the gitignored `.grove/config.local.json`) and user-global `~/.claude/`, never in the committed tree.

## Project structure

| Path | Purpose | Guide |
|---|---|---|
| `src/grove/core/` | engine: lifecycle, manager, config cascade, store, status, registry, activity, CLI | [core/CLAUDE.md](src/grove/core/CLAUDE.md) |
| `src/grove/core/contracts/` | wire-level Pydantic shapes that cross clients | [contracts/CLAUDE.md](src/grove/core/contracts/CLAUDE.md) |
| `src/grove/core/agents/` | tool-agnostic agent introspection (adapters) | [agents/CLAUDE.md](src/grove/core/agents/CLAUDE.md) |
| `src/grove/daemon/` | loopback FastAPI daemon (multi-repo, SSE) | [daemon/CLAUDE.md](src/grove/daemon/CLAUDE.md) |
| `src/grove/client/` | transport-agnostic attach (local PTY / SSH) | [client/CLAUDE.md](src/grove/client/CLAUDE.md) |
| `src/grove/tui/` | Textual terminal UI | [tui/CLAUDE.md](src/grove/tui/CLAUDE.md) + [design-system.md](docs/design-system.md) |
| `webapp/` | read-only Next.js dashboard | [webapp/CLAUDE.md](webapp/CLAUDE.md) |
| `docs/` | published mkdocs site | [docs/CLAUDE.md](docs/CLAUDE.md) |
| `packaging/` | systemd-user service units | [packaging/CLAUDE.md](packaging/CLAUDE.md) |
| `tests/` | pytest suite + CI/lint gotchas | [tests/CLAUDE.md](tests/CLAUDE.md) |

Side-effect surfaces live in `core/git.py` and `core/tmux.py` only; everything else is pure. The package root (`__init__.py` re-exports) is the contract; modules below it are internal even without an underscore.

## The distributed CLAUDE.md tree

Working memory is **distributed and recursive**: every component owns a nested `CLAUDE.md` holding its engineering decisions AND its session lessons. Read the deepest file that applies before editing there. An agent working one component loads only the files on its path, never the whole tree. This faceting is deliberate — it lets a **fleet of agents take disjoint roles and work asynchronously**, each loading only its own slice instead of one monolithic doc that fills the context window.

**Recursive management — every file links both ways, so nothing is lost.** Every nested file opens with a `> ↑ parent · root` backlink; every parent lists its children (the map below). Maintain both links whenever you add, move, or rename a file. This root carries only cross-cutting principles, structure, and process lessons — never restate a nested file's content here. Write each lesson in the nearest owning file; promote a lesson up a level only when it becomes genuinely cross-cutting.

```
/CLAUDE.md  (this file — principles · structure · process lessons)
├─ src/grove/core/CLAUDE.md            engine: lifecycle · config · side effects · status · registry · activity
│  ├─ src/grove/core/contracts/CLAUDE.md   the wire boundary (Pydantic shapes that cross clients)
│  └─ src/grove/core/agents/CLAUDE.md       tool-agnostic agent introspection (adapters)
├─ src/grove/daemon/CLAUDE.md          loopback HTTP daemon (multi-repo, SSE)
├─ src/grove/client/CLAUDE.md          transport-agnostic attach (local PTY / SSH)
├─ src/grove/tui/CLAUDE.md             Textual TUI   (+ docs/design-system.md = visual contract)
├─ webapp/CLAUDE.md                    read-only Next.js dashboard
├─ docs/CLAUDE.md                      published mkdocs site
├─ packaging/CLAUDE.md                 systemd-user service units
└─ tests/CLAUDE.md                     test conventions · CI/lint gotchas
```

Planning lives in Gitea issues (epics plus per-agent stories) — keep design knowledge in this tree, plans in issues. Don't dump design detail into issues, or plans into the tree.

## Engineering principles

These apply across every module. Some areas already follow them tightly; others are evolving. The bar isn't perfection — every change should leave the surrounding code more maintainable than it found it. Small decreases in code health compound into rewrites; small improvements compound into a codebase a new engineer can join in a week.

- **Modules align with concerns, not technical layers — Single Responsibility Principle at module scale.** Each module should answer one question, nameable in a single sentence. If you can't name it, it's drifting toward a junk drawer — split the concerns out, or fold the module into its real owner. Boundaries follow what changes together, not generic "models / views / controllers" buckets.
- **Public surface is small and explicit.** A package's entry point is its contract; leading underscores on internal modules signal "don't import from here". Explicit re-export lists pin what consumers may depend on. The smaller the public surface, the cheaper internal refactors become.
- **Deterministic public entry points; subpackages organize by concern, not by technical layer.** Each package exposes one canonical surface (its `__init__.py` re-export list); every module under it is internal. Split by concern (`contracts/` for wire shapes, `git.py` / `tmux.py` for side effects, `manager.py` for orchestration), never by technical layer — no generic `helpers/` / `utils/` / `models/` / `services/` junk drawers. When one file accumulates two answers to two questions, split it. The directory tree should read as a map you can navigate without grep.
- **Dependencies flow inward.** Orchestration imports from utilities; the reverse is a smell. When a low-level helper has to know about a high-level caller, the boundary is wrong — most circular-import pain traces back to this.
- **Boring code beats clever code.** Reuse the pattern already established. Local cleverness has a price every reader pays; if you must deviate, name the reason inline.
- **Add structure only when there's a real concern to separate (YAGNI).** Build only what the current requirement demands; don't abstract on speculation. Three similar lines is fine. A new helper, class, or subpackage costs review surface for years — pay only when one engineer can plausibly own the new boundary.
- **Strong types where they catch bugs.** Narrow literal types for string sets that drive branching, structured types for conditional payloads, explicit return types everywhere. Escape-hatch types only for genuinely heterogeneous external data, narrowed at the boundary. Introduce protocols only when more than one real implementation exists.
- **Pydantic at public-contract boundaries; plain dataclass for in-process state.** Pydantic for anything that crosses a client/server boundary now or could later (config, requests, responses, discriminated unions of intent). Plain `@dataclass(slots=True)` for internal mutable state and engine-only IR. The test: would a non-Python client ever construct or receive this? Yes → Pydantic; no → dataclass. Wire shapes live in [`core/contracts/`](src/grove/core/contracts/CLAUDE.md).
- **Class-encapsulated atomic state — code as poem, not a junk drawer of free helpers.** State and the methods over it live together as one class. Prefer instance / `@classmethod` / `@staticmethod` on the owning type over root-level free functions, especially private ones. A free helper in module scope is usually a missing class. When you touch a module, leave its class structure a little tighter than you found it.
- **Side effects at the edges, pure logic in the middle.** I/O, network, and time-of-day belong at the boundary (handlers, fetchers, drivers); the decision logic between them stays testable without them. Best-effort side effects isolate their failures: bounded timeout, structured log per outcome, never re-raise into the caller's retry path.
- **Tests pin contracts, not implementation.** When a test patches a private symbol, that path becomes an implicit contract — moving it silently no-ops the patch while the test still passes. Surface the seam publicly or update the test in the same commit.
- **Comments and docstrings explain WHY, not WHAT.** Names and types document the what; prose carries the constraint, the trade-off, the past incident, the surprising invariant. Write the docstring's first line for the engineer deciding whether to call this.
- **KISS and DRY are the core philosophy.** Bias toward less code. Before writing anything custom, search for an existing library or an existing utility in the codebase. Proven library for infrastructure; custom code only for business logic.
- **Mechanism, not policy — configuration cascades at the consumption surface.** For the user-facing surface (framework, prompt, model, workflow), provide mechanism and leave policy to the user. Defaults are sensible but held lightly; any value a developer might reasonably change is reachable from outside the code. Config layers by specificity: built-in → project → team → machine → user → invocation. This is only sustainable because the internal codebase stays strict on DRY and KISS — one boring implementation inside, the override cascade resolving on top.

### The provider boundary (LLMs and agents)

- **Patch the provider boundary, never model behavior.** Treat LLMs and agents as non-deterministic black-box APIs; avoid anthropomorphic language. Write code only for provider and model *parameter and protocol* differences — how an invocation is launched, how tool calls are passed, how responses (and their varied content-block types) are received, normalized, and presented, accurately across every type exchanged. Never add code to correct, second-guess, or work around what a model *does*: that output is black-box. An adapter normalizes *shape*, not *semantics*. The concrete adapter layer is [`core/agents/`](src/grove/core/agents/CLAUDE.md).

### Other rules

- Code validates itself at the point of definition (schema validators, strict configs that forbid unknown fields).
- Define logic once; call everywhere. A rule reused by more than one caller lives in one place.
- Smallest diff that solves the problem. No speculative abstractions.
- Keep published contracts stable: interface names, method signatures, and field names don't move under consumers without coordination.
- Tests prefer real code paths; stub only I/O boundaries. Cover full orchestration loops with in-memory fakes for external services and model output.
- Gitmoji plus Conventional Commits (e.g. `✨ feat(scope): ...`).
- Keep this file lean as the project evolves; push detail down into the owning nested file.

## Running, testing, linting

- Install (editable, with the daemon extra): `uv tool install --reinstall --force --editable '.[daemon]'` from the repo root, then `systemctl --user restart grove-daemon`.
- Tests: `uv run pytest`. Lint: `make lint` (runs **both** `ruff format --check` and `ruff check` — always the full target before pushing, never just `ruff check`). Types: `uv run mypy src`. Architecture: import-linter.
- CI is Linux-only; cross-platform defenses are unverified by CI. See [tests/CLAUDE.md](tests/CLAUDE.md) for the test seams and the Windows/macOS gotchas to reason about by hand.

## Cross-cutting process lessons (no single component owner)

> Workflow and tooling lessons with no component home. Component-specific learnings live in the nested files mapped above — don't re-log them here.

- **Running Grove from a checkout means an editable install; an update refreshes three surfaces.** `~/.local/bin/grove` resolves to whatever was last `uv tool install`ed; a vanilla install pulls the published PyPI wheel (the release), not your checkout. Symptom: a new endpoint or method is green in `pytest` but the running daemon serves 404 / `No such command`. Fix: `uv tool install --reinstall --force --editable '.[daemon]'`, then restart the daemon (the `[daemon]` extra is load-bearing). Updating Grove means refreshing the CLI/TUI (relaunch), the daemon (restart), and the webapp (rebuild `.next` + restart) — each is a separate surface.
- **A Grove workspace is a git worktree, so it sees only committed files.** Uncommitted working-tree edits never propagate into a worktree. Commit shared config (e.g. `.mcp.json`, whose tokens are env refs, not literals) for worktrees to inherit it. Config layers: user `~/.config/grove/config.json`, committed project `.grove/config.json`, gitignored `.grove/config.local.json`.
- **Parallel-agent build pattern.** Build the shared foundation solo and verify it, then fan out one agent per *disjoint* directory. Agents only consume the foundation, never edit it → zero conflicts. Give each the exact contract plus the source to match; integrate and verify last. You own the shared contention points solo. (This very doc tree was built that way.)
- **The working tree can advance under you mid-session (concurrent dev).** If the Edit "modified since read" guard fires, re-read the file fresh and re-derive the edit against current content — never force it. `git log` / `git diff --stat` to map the real blast radius before integrating.

## IMPORTANT

Continuously capture non-trivial lessons in the **nearest owning `CLAUDE.md`** (the tree above), so future sessions accelerate and load only what they need. Keep every file in its lane; keep parent and child links current.
