# grove.core.contracts — the wire boundary (Pydantic shapes that cross clients)

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

This package is the single coupling point between the engine and every client (TUI, daemon, webapp). It holds the wire-level Pydantic shapes only: request bodies, response Views, and discriminated-union intents. Engine state stays in plain dataclasses elsewhere; nothing here imports a client module, and no client imports the engine's dataclasses directly.

## Pydantic outward, dataclass inward

**Decide each type by one question: would a non-Python client ever construct or receive this? Yes → Pydantic, here. No → plain dataclass, in the engine.** (Condensed from the root [engineering principles](../../../../CLAUDE.md).) Pydantic for anything crossing a client/server boundary now or later — config, requests, responses, intents. Plain `@dataclass(slots=True)` for in-process mutable state (`WorkspaceState`) and intermediate IR that never leaves the engine (`ResolvedBranch`, `WorkspaceEvent`). `contracts/` is the canonical home for the wire shapes; never import them from a TUI module — they belong to the engine and travel outward.

## Discriminated unions for intent

**Model "what does the user want" as a Pydantic discriminated union, never a dataclass.** `BranchPlan` (Auto / NewNamed / ExistingLocal / TrackRemote / RootBranch) is the canonical example: the TUI builds a variant today, a future API server receives it as JSON, a web client builds it from form fields — that boundary is exactly why it is Pydantic. Each variant carries a `kind: Literal["..."]` tag plus its own fields; the union is `Annotated[Union, Field(discriminator="kind")]`, so Pydantic v2 dispatches by tag and generates JSON Schema for the whole tree, with `extra="forbid"` catching typos. The same shape fits any future intent (attach plans, rename plans).

Inside the engine each variant has a `resolve(cfg, title, ts) → ResolvedBranch` method; `ResolvedBranch` is a plain frozen dataclass because it never crosses a wire. `RootBranch` (the fifth variant, `kind="root"`, no user fields) lives here too — but the placement *gate* that acts on it (skip every worktree side effect, force `delete_branch=False`) is engine policy in [grove.core](../CLAUDE.md). Keep any inline `Annotated[Union, Field(discriminator=...)]` at module scope, never in a closure — the daemon's `TypeAdapter` rebuild can't see closure-scoped types.

## Views: serialize, never expose

**Every HTTP response shape is a Pydantic View in `views.py`, never the engine dataclass directly.** `WorkspaceStateView` / `WorkspacePeekView` / `AttachInstructionView` / `CommitSummaryView` exist solely to serialize engine dataclasses across HTTP. Each has a `from_*` classmethod that adapts its dataclass; `frozen=True` catches accidental mutation. Internal-only fields (`init_log_path`, `init_env`) deliberately do NOT appear on the wire. New endpoint whose response shape isn't covered → add a View; never widen the wire by leaking a dataclass. Activity wire types (`DashboardEvent` + `*View` mirrors) live in `activity.py` and import the engine activity dataclasses under `TYPE_CHECKING` only, so a contracts import never drags the manager/registry in.

Session-exploration wire types (`SessionSummaryView` / `SessionTurnView` / `SessionDetailView`) live in `sessions.py` — a separate module because history reads are a different concern from the live dashboard. Two invariants: they are **fetch-on-demand only, never embedded in the SSE `DashboardEvent`** (turns are unbounded where the stream payload must stay small), and `transcript_path`/`cwd` stay off the wire (host-private paths; clients identify a session by id). Per-entry text is capped (~4 KB, trailing ellipsis is the trim signal) so one mega-turn can't ship a multi-MB body.

## Session lessons

- **Pydantic v2's default regex engine (Rust) has no lookahead / lookbehind.** The natural `^(?!-)[A-Za-z0-9._/\-]+$` for "no leading dash" raises `SchemaError: look-around ... is not supported` at class-construction time. Split the character class instead: `^[A-Za-z0-9._/][A-Za-z0-9._/\-]*$` matches the same set without lookahead. Same trap for any `(?=…)` / `(?<…)` / non-greedy edge case. If a field genuinely needs lookaround, switch its engine via `Field(..., pattern_engine="python-re")` (Pydantic v2.10+).
