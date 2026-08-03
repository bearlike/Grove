# Grove — Agent Guide

Grove lets you tend multiple git worktrees like branches in a forest: spin up, switch between, and tear down isolated coding sessions without losing your place. See `README.md` for the product brief.

## Handling a handed-off issue (the autonomous workspace loop)

When you own a tracker issue end-to-end, drive it to a merged PR on this loop. Bias to action — never over-plan, over-spec, ask trivial questions, or invent fallbacks nobody requested.

1. **Read the issue, map the components, implement.** Read the nearest owning `CLAUDE.md`(s) on the path you'll touch *before* editing.
2. **Keep the issue number OUT of the PR title and body.** PR text and squash titles mirror to the public GitHub remote, where a bare `#<n>` resolves against a *different* tracker — so `Fixes #<n>` would both leak the private issue graph and auto-close nothing.
3. **Check the open PR for conflicts with the remote target and resolve them** — rebase onto the freshly-fetched target, re-apply onto the *current* structure (don't clobber what landed meanwhile), re-run the gates, force-push.
4. **Reply on the issue naming the PR — that comment IS the linkage, since the PR carries none.** Summarize the design decision, the components touched, and the gate results. Say plainly anything that did not work, anything left out, and any acceptance criterion that turned out to be unsatisfiable — a ticket whose own premise was wrong is the most valuable thing you can leave behind, and the next session cannot recover it from the diff. Close the issue by hand once the PR merges: an open ticket with a linked PR is honest, a closed one with unmerged work is not.
5. **After implementing, fold the durable insights into the nearest owning `CLAUDE.md`** — the invariant, the trade-off, the reference you'd otherwise re-derive; never copyable snippets or restatements of code.

**Parallelize** — fan a fleet of sub-agents out over disjoint work per the parallel-agent pattern below.

## Core operating rules

- Separate facts from assumptions and keep updating both; prefer direct evidence (run the code, read the real output) over inference from a description. Ask the user about ambiguous assumptions instead of guessing silently.
- Use absolute `YYYY-MM-DD` dates in findings you report to a human, never relative ones ("last week").
- Do not push to a remote unless explicitly asked.
- **Plans live in the issue tracker; design knowledge lives in this memory tree.** Session-local working memory — scratch plans, specs, agent reports — is never committed. Don't dump design detail into issues, or plans into the tree. An unlabelled issue is invisible to the board, so label every issue you file.
- **Never leak host-private details into tracked files.** No absolute home paths, real private repo names, personal profile names, or host/infra specifics in committed docs or code — this repo mirrors to a public remote. Keep examples generic and fictional. Real host and profile configuration lives in your own config (`~/.config/grove/`, the gitignored `.grove/config.local.json`) and user-global `~/.claude/`, never in the committed tree.

## Project structure

| Path | Purpose | Guide |
|---|---|---|
| `src/grove/core/` | engine: lifecycle, manager, config cascade, store, status, registry, activity, CLI | [core/CLAUDE.md](src/grove/core/CLAUDE.md) |
| `src/grove/core/contracts/` | wire-level Pydantic shapes that cross clients | [contracts/CLAUDE.md](src/grove/core/contracts/CLAUDE.md) |
| `src/grove/core/agents/` | tool-agnostic agent introspection (adapters) | [agents/CLAUDE.md](src/grove/core/agents/CLAUDE.md) |
| `src/grove/core/tickets/` | branch-aware ticket providers (Gitea/GitHub/Linear) | [tickets/CLAUDE.md](src/grove/core/tickets/CLAUDE.md) |
| `src/grove/core/issueops/` | issue-comment events → workspace actions (command grammar + routing) | [issueops/CLAUDE.md](src/grove/core/issueops/CLAUDE.md) |
| `src/grove/core/notifications/` | push notifications on workspace edges (broker + channels) | [notifications/CLAUDE.md](src/grove/core/notifications/CLAUDE.md) |
| `src/grove/daemon/` | loopback FastAPI daemon (multi-repo, SSE) | [daemon/CLAUDE.md](src/grove/daemon/CLAUDE.md) |
| `src/grove/client/` | transport-agnostic attach (local PTY / SSH) | [client/CLAUDE.md](src/grove/client/CLAUDE.md) |
| `src/grove/mcp/` | MCP server (stdio tools over the client SDK) | [mcp/CLAUDE.md](src/grove/mcp/CLAUDE.md) |
| `src/grove/tui/` | Textual terminal UI | [tui/CLAUDE.md](src/grove/tui/CLAUDE.md) + [design-system.md](docs/design-system.md) |
| `webapp/` | Next.js dashboard (drives lifecycle + steering; the `/sessions` browser is structurally read-only) | [webapp/CLAUDE.md](webapp/CLAUDE.md) |
| `docs/` | published mkdocs site | [docs/CLAUDE.md](docs/CLAUDE.md) |
| `packaging/` | systemd-user service units + clean-install smoke | [packaging/CLAUDE.md](packaging/CLAUDE.md) |
| `tests/` | pytest suite + CI/lint gotchas | [tests/CLAUDE.md](tests/CLAUDE.md) |

## The distributed CLAUDE.md tree

**Read the deepest file that applies before editing there, and write each lesson in the file that owns it** — promote one up a level only when it becomes genuinely cross-cutting. This root carries only cross-cutting principles, structure, and process lessons; it never restates a nested file's content. Every nested file opens with a `> ↑ parent · root` backlink and every parent lists its children — maintain both links whenever you add, move, or rename a file.

```
/CLAUDE.md  (this file — principles · structure · process lessons)
├─ src/grove/core/CLAUDE.md
│  ├─ src/grove/core/contracts/CLAUDE.md
│  ├─ src/grove/core/agents/CLAUDE.md
│  ├─ src/grove/core/tickets/CLAUDE.md
│  ├─ src/grove/core/issueops/CLAUDE.md
│  └─ src/grove/core/notifications/CLAUDE.md
├─ src/grove/daemon/CLAUDE.md
├─ src/grove/client/CLAUDE.md
├─ src/grove/mcp/CLAUDE.md
├─ src/grove/tui/CLAUDE.md      (+ docs/design-system.md = the visual contract)
├─ webapp/CLAUDE.md
├─ docs/CLAUDE.md
├─ packaging/CLAUDE.md
└─ tests/CLAUDE.md
```

## Engineering principles

- **Modules align with concerns, not technical layers.** Each module answers one question, nameable in a sentence; if you can't name it, split it or fold it into its real owner. Split by concern (`contracts/` for wire shapes, `git.py` / `tmux.py` for side effects, `manager.py` for orchestration) — never generic `helpers/` / `utils/` / `models/` / `services/` buckets. When one file accumulates two answers to two questions, split it.
- **Public surface is small and explicit.** A package's `__init__.py` re-export list is its contract; every module under it is internal, and a leading underscore says so. The smaller the public surface, the cheaper internal refactors become.
- **Dependencies flow inward.** Orchestration imports utilities; the reverse is a smell, and most circular-import pain traces back to it.
- **Boring code beats clever code.** Reuse the established pattern; if you must deviate, name the reason inline.
- **Build only what the current requirement demands (YAGNI, KISS, DRY).** Three similar lines is fine; a new helper, class, or subpackage costs review surface for years. Bias toward less code, and search for an existing library or an existing utility before writing anything custom. A rule reused by more than one caller lives in exactly one place.
- **Fix at the smallest generic seam; never overfit, never hard-code policy.** Before adding a typed field, a new parameter threaded through layers, or a special case, check whether an existing mechanism already covers it — the config cascade, an `env`-style map, an existing scan/union, a field already on the model. **A fix that balloons across many files or duplicates an existing capability is the signal to stop and find the seam.** A specific variable name, profile, path, or provider quirk belongs in config, never in a default constant or a branch — code supplies the *mechanism* that acts on whatever config names.
- **Mechanism, not policy — configuration cascades at the consumption surface.** Any value a developer might reasonably change is reachable from outside the code; defaults are sensible but held lightly. Config layers by specificity: built-in → project → team → machine → user → invocation. This is only sustainable because the code underneath stays strict on DRY and KISS — one boring implementation, the override cascade resolving on top.
- **Strong types where they catch bugs.** Narrow literal types for string sets that drive branching, structured types for conditional payloads, explicit return types everywhere. Escape-hatch types only for genuinely heterogeneous external data, narrowed at the boundary. Introduce a protocol only when more than one real implementation exists.
- **Pydantic at public-contract boundaries; plain `@dataclass(slots=True)` for in-process state.** The test: would a non-Python client ever construct or receive this? Yes → Pydantic; no → dataclass. Wire shapes live in [`core/contracts/`](src/grove/core/contracts/CLAUDE.md).
- **Class-encapsulated atomic state.** State and the methods over it live together as one class (instance / `@classmethod` / `@staticmethod`, inheritance + DI). A free helper in module scope is usually a missing class.
- **Side effects at the edges, pure logic in the middle.** I/O, network, and time-of-day belong at the boundary (handlers, fetchers, drivers); the decision logic between them stays testable without them. Best-effort side effects isolate their failures: bounded timeout, structured log per outcome, never re-raise into the caller's retry path.
- **Tests pin contracts, not implementation.** When a test patches a private symbol, that path becomes an implicit contract — moving it silently no-ops the patch while the test still passes. Surface the seam publicly or update the test in the same commit. Tests prefer real code paths and stub only I/O boundaries; cover full orchestration loops with in-memory fakes.
- **Comments and docstrings explain WHY, not WHAT.** Names and types document the what; prose carries the constraint, the trade-off, the surprising invariant. Write the docstring's first line for the engineer deciding whether to call this.
- **Code validates itself at the point of definition** — schema validators, strict configs that forbid unknown fields.
- **Keep published contracts stable:** interface names, method signatures, and field names don't move under consumers without coordination.
- Gitmoji plus Conventional Commits (e.g. `✨ feat(scope): ...`).

### Concurrency

Grove is one event loop (daemon), one UI thread (TUI), and a lot of slow blocking I/O — git, tmux, `docker exec`, `devcontainer up`. These rules are ordered: satisfy the earlier ones first.

- **Nothing blocking runs on the loop or the UI thread.** A `devcontainer up` is minutes, and on the loop those minutes freeze every SSE stream, every other repo's dashboard and the activity poll; on Textual's single thread they freeze every timer, keypress and repaint. Off-thread is `asyncio.to_thread` — hand-rolling `get_running_loop()` + `run_in_executor(None, …)` is the same call with more lines and no contextvars.
- **Work whose duration is unbounded gets its own bounded pool.** The default executor is the render path; sharing it with lifecycle verbs trades a loop stall for pool starvation, which looks identical to the user. Bound the pool so a fleet can never become N simultaneous image builds, name its threads, and never let shutdown wait on side-effecting work already in flight.
- **Introducing concurrency deletes an invariant. Restore it explicitly.** A single-threaded loop is an implicit mutex over everything it runs; the moment work moves to a pool that mutex is gone and nobody notices, because the code that relied on it never mentioned it. Re-establish exclusion at the **narrowest key that preserves the point of the change** — per workspace, not global, or the pool bought nothing. Atomic *storage* is not the same guarantee: `JsonWorkspaceStore` locks its read-modify-write, but a verb is `read → mutate git/tmux/container → save`, and the exposure is that middle span.
- **Results cross a thread boundary as messages, never as direct writes**, and a verb dispatched per keypress needs de-duplication or one impatient double-press is two kills.
- **Prefer not having a loop at all.** In order: (1) subscribe to an edge that already exists; (2) if you must poll, gate it on a live consumer — publishing into an empty room is pure waste; (3) suspend the tick when its surface isn't visible, but **measure which branch actually leaks before gating**, and never gate on window focus (an unfocused Grove is a legitimate live surface); (4) only then, a timer, with its body gated so an idle fleet costs nothing.

### The provider boundary (LLMs and agents)

- **Patch the provider boundary, never model behavior.** Treat LLMs and agents as non-deterministic black-box APIs; avoid anthropomorphic language. Write code only for provider and model *parameter and protocol* differences — how an invocation is launched, how tool calls are passed, how responses (and their varied content-block types) are received, normalized and presented. Never add code to correct, second-guess, or work around what a model *does*: an adapter normalizes *shape*, not *semantics*. The concrete adapter layer is [`core/agents/`](src/grove/core/agents/CLAUDE.md).

## Running, testing, linting

- Install (editable, all surfaces): `uv tool install --reinstall --force --editable '.[all]'` from the repo root, then `systemctl --user restart grove-daemon`. `[all]` = daemon+client+mcp; `'.[daemon]'` is the lean daemon-only variant that intentionally omits the MCP SDK and leaves `grove-mcp` non-functional (`ModuleNotFoundError: No module named 'mcp'`).
- Tests: `uv run pytest`. Lint: `make lint` is the full gate — `ruff check`, `ruff format --check`, `mypy src/grove`, `lint-imports` in sequence. Always run the full target before pushing, never just `ruff check`.
- CI is Linux-only; cross-platform defenses are unverified by CI. See [tests/CLAUDE.md](tests/CLAUDE.md) for the Windows/macOS gotchas to reason about by hand.

## Cross-cutting process lessons (no single component owner)

> Workflow and tooling lessons with no component home. Component-specific learnings live in the nested files mapped above — don't re-log them here.

- **A checkout means an editable install, and an update refreshes three independent long-lived surfaces.** `~/.local/bin/grove` resolves to whatever was last `uv tool install`ed, and a vanilla install pulls the published wheel, not your checkout — symptom: a new endpoint or method is green in `pytest` but the running daemon serves 404 / `No such command`. Refresh the CLI/TUI (relaunch), the daemon (restart) and the webapp (rebuild `.next` + restart) separately. The opt-in `grove-mcp` server needs no refresh: its client respawns it per connection, so an editable pull is live on the next launch; only a changed `mcp` extra needs a reinstall. See the `reinstalling-grove` skill.
- **Never run a Grove entrypoint as root — it poisons the editable install, and uv's error never says so.** The `uv tool` venv and (editable install ⇒) the checkout's `src/grove/**/__pycache__` are user-owned; one root-run entrypoint — classically a root shell where `claude` spawns `grove-mcp` from `.mcp.json` — makes CPython write uid-0 bytecode into both. `uv tool install --reinstall` must empty `site-packages`, cannot unlink a root-owned `__pycache__`, and dies with a bare `Permission denied` naming whatever dependency it hit first; nothing points at root. **Diagnose by ownership, not by the message:** `find "$(uv tool dir)/grove" ! -user "$(id -un)"` — and the *set* of root-owned `.pyc` names the guilty entrypoint (only `grove-mcp` pulls in `mcp/**`). **Recover without sudo** by renaming the venv aside (`mv "$(uv tool dir)/grove" …` — rename needs write on the *parent*, not on the root-owned contents) and reinstalling; the stale tree still needs one `sudo rm -rf`. `reinstall.sh` preflights this and prints the `chown` remedy.
- **A Grove workspace is a git worktree, so it sees only committed files.** Uncommitted working-tree edits never propagate into a worktree — commit shared config (e.g. `.mcp.json`, whose tokens are env refs, not literals) for worktrees to inherit it. Config layers: user `~/.config/grove/config.json`, committed project `.grove/config.json`, gitignored `.grove/config.local.json`.
- **Parallel-agent build pattern: build the shared foundation solo and verify it, then fan out one agent per *disjoint* directory.** Agents only consume the foundation, never edit it → zero conflicts. This scales to a whole epic when each story owns its own new module and is restricted to small, delimited insertions in the shared files it must report exactly; merge the engine/integration stories last. **When a redesign instead forces many agents into *shared* atoms, freeze the public contract first** — props, testids, first-child structure — so each agent restyles internals freely and the integrations compose. The contract, not the directory, is the conflict boundary.
- **A delegated agent optimizes exactly what you made MEASURABLE, so a format rule plus an impression of size produces format compliance and nothing else.** Four agents told "strict bullets, at most one lead paragraph per section" and "roughly half the length" delivered the paragraph rule perfectly and cut prose by 9%: the bullets were the same sentences with dashes in front. Re-nudging did not help, because the second instruction was no more measurable than the first. **Give a per-unit budget with the invariant beside it** — "this section is 496 words, bring it under 250, here is the fact list that must survive" — and verify with a diff of the tokens that had to survive rather than by reading. The integrator's own hand-pass then closed the gap in one turn, which is the tell that the work was never the hard part.
- **Give each concurrent agent its own worktree AND a file-ownership list.** One checkout cannot hold six branches — a fleet told to `git checkout -b` in a shared tree produces interleaved commits on whichever branch happened to be current. Isolation alone is not enough: name the files each agent owns, forbid the rest, and require it to REPORT any unavoidable touch outside its list with exact lines. When a shared file does conflict, the resolution is usually the UNION of both intents, not either side whole — taking one side silently reverts the other's fix.
- **A delegated agent can FINISH the work and stall on reporting — read its artifacts before you nudge it.** Nudging costs a full context replay and changes nothing; `git -C <worktree> log/status/diff` answers in one call. Under a saturated fleet a slow suite is the HOST, not a hang — and a run killed by memory pressure exits 144, which is not a test failure and must not be reported as one. Corollary for the integrator: **gate the branch yourself rather than waiting to be told it is green.**
- **The working tree can advance under you mid-session.** If the Edit "modified since read" guard fires, re-read the file and re-derive the edit against current content — never force it.
- **`src/grove/skills/` is published twice, and the copy users install is the one in the separate plugin-marketplace repo.** `grove skills install` writes from this tree, but everyone who installs the plugin gets the marketplace copy — so a skill edited only here is live for us and stale for them, silently and indefinitely. **Edit both in one change and `diff` them before committing**; the plugin side also needs its `plugin.json` + root `marketplace.json` version bumped in lockstep and its hand-maintained README updated. A skill's frontmatter `name` must equal its `skills/<name>/` directory name.
- **A skill or guide that documents a tool surface goes stale the moment the surface grows, and nothing fails when it does.** No test, gate or type checker reads prose, so the only detector is a periodic audit against the real definitions — the Typer commands and MCP registration tuples themselves, never a README. **Never write a count you would have to maintain**; point at the enumeration that is the census instead.
