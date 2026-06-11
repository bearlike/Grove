# grove.core.agents — tool-agnostic agent introspection (AgentAdapter + ClaudeCodeAdapter)

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

This package reads an agent tool's native transcripts and normalizes them to the `AgentActivity` / `AgentSession` model (plain frozen dataclasses in `model.py`). Clients and the `ActivityService` consume only the normalized model — never a tool's raw transcript. The `AgentAdapter` Protocol is the seam; `ClaudeCodeAdapter` is the only impl today.

## The provider-boundary rule

**Patch the provider boundary, never model behavior.** Grove writes code only for provider/model *parameter and protocol* differences: how an invocation is launched, how tool calls are passed, how responses (and their varied content-block types) are received, normalized, and presented — accurately, across every type exchanged. It never adds code to correct, second-guess, or work around what a model *does* — that output is non-deterministic black-box (treat it as such, per [root](../../../../CLAUDE.md)). An adapter normalizes *shape*, not *semantics*. Adding a new tool (codex / opencode) is one adapter module + one `registry.get_adapter` entry; clients and the `ActivityService` keep consuming only the normalized model.

## Claude transcript parsing

**Encode these hard-won facts; each is a trap if forgotten** (verified against real on-host JSONL, Claude Code 2.1.x):

- **A `type:"user"` line is usually NOT a human turn.** `tool_result` blocks carry `role:"user"` (one real session: 4683 user lines, 80 real turns). The real-turn filter is the whole game: not sidechain/meta, no `tool_result` block, text free of `<command-*>` / `<bash-*>` / `Caveat:` / compaction markers.
- **Locate transcripts by globbing the session UUID, never by decoding the project folder name.** The cwd encoding (**every non-alphanumeric char → `-`**, per the official Agent SDK sessions guide — not just `/` `.` `_`) is lossy and irreversible (anthropics/claude-code#7009). Confirm the match via each line's `cwd`.
- **Status comes from the tail assistant `stop_reason`.** `tool_use` → working, `end_turn` / `stop_sequence` → waiting. No LLM needed.
- **De-dup resume/fork overlap by `(message.id, requestId)`.** Cache-read / creation tokens fold into "in".
- `replies_per_turn` is the per-turn breakdown: `human_turns == len(replies_per_turn)`, `assistant_replies == sum`.
- **Defensive parsing:** per-line `try/except`, tolerate `FileNotFoundError`, and **coerce booleans that arrive as strings** (`isSidechain:"false"`) — never `bool(raw)`.
- `transcript_digest()` is the reserved seam for the future external-LLM interpreter (#20); it strips `tool_result` payloads.

## Deterministic session correlation

**Grove mints the session id and launches with it; it never scans to guess.** `WorkspaceManager.create()` mints a canonical dashed UUID (Claude requires RFC-4122, distinct from the bare-hex workspace id), asks the adapter for a `launch_decoration` (`["--session-id", uuid]` for Claude Code, `[]` for generic/shell), threads it through `tmux.build_workspace_layout` (shell-quoted), and persists `WorkspaceState.agent_session_id`. So the transcript path is known by construction.

- **The `--session-id` is never hard-coded in `tmux.py`** — it comes from the adapter. `AgentSpec.kind: Literal["claude_code","generic"]` selects the adapter (built-in `claude` ships `claude_code`).
- **resume keeps the persisted id (continue); respawn mints a fresh id (new session).** That fork lives in exactly one helper (`_launch_decoration` in `manager.py`) so the two verbs can't drift.
- `manager.primary_transcript(id)` resolves the file(s) via the adapter, returning a `tuple` (snapshot convention; also dodges the `list`-method-shadows-`list[]`-builtin mypy trap inside `WorkspaceManager`).

## Push status — hook sidecar (#18)

**A per-session sidecar that a Grove-managed Claude Code hook writes; it overrides the polled blend, and installs without touching the user's settings.** Polling (`stop_reason` + tmux) can't separate waiting-from-done and is blind to a permission prompt; hooks push exact lifecycle events.

- `hook.py::ClaudeHook` maps each event name to an `AgentActivityState`: `Notification` → BLOCKED (the signal polling can't see), `Stop` → WAITING, `SessionStart` / tool events → WORKING, `SubagentStop` → None (a finishing sub-agent never flips the main thread). It writes `<state-dir>/agent-sidecars/<session_id>.json`.
- The hidden `grove agent-hook` CLI reads hook JSON on stdin (+ `$TMUX_PANE`) and writes the sidecar — **always exits 0** (a hook must never fail the agent).
- The reader (`ActivityService._session_activity`, policy in [grove.core](../CLAUDE.md)) uses the sidecar state only when **fresh** (`DEFAULT_SIDECAR_MAX_AGE_SECONDS`, 300s); stale ⇒ session went quiet ⇒ defer to polled.
- **Install is additive and non-clobbering.** `cfg.hooks.enabled` makes the manager write a Grove-owned hook-only settings file and append `--settings <that file>` to the claude launch. The user's `.claude/settings.json` is never edited; uninstall is flipping the flag back. No hook installed ⇒ no sidecar ⇒ polled status stands unchanged.

## Session exploration (list / turns / dump — issue #26)

**The adapter's read surface mirrors the official Agent SDK, without taking the dependency.** The SDK's `list_sessions()` / `get_session_messages()` / `get_session_info()` read `projects/` JSONL directly from disk (no CLI invocation) — validating Grove's approach. `SessionSummary` deliberately mirrors `SDKSessionInfo` field names (`first_prompt`, `git_branch`, `cwd`, `created_at`); we keep our own reader because it already handles what the SDK doesn't (multi-config-dir cascade, string-boolean coercion, preamble cwd scan).

- **`discover_paths` is the one scan; `discover` (ids) and `list_sessions` (summaries) are projections of it.** Don't add a third scan variant — extend the tuple.
- **`list_sessions` builds each summary + its point-in-time `AgentActivity` from ONE parse of the main transcript** (sub-agent files are sidechain detail, excluded from summary scope). The full parse per file is the same cost the dashboard already pays per tick — don't prematurely swap in head/tail bounded scans; revisit only if listing measurably lags.
- **`last-prompt` records come in two shapes** (verified on-host): with `lastPrompt` text, and leafUuid-only pointers. `last_prompt_text()` skips the pointer form — treating it as an empty prompt loses the real one.
- **`read_turns` groups assistant entries (text + tool calls, in block order) under each human turn.** Assistant records preceding any human turn (resumed/compacted head) collect under a leading turn with empty `user_text` — never dropped.
- **Watch-item: forked sessions now store a parent *pointer* and hydrate on read** (upstream changelog; the SDK rebuilds chains via `parentUuid`). No pointer records observed on-host yet — if fork-heavy transcripts start showing truncated history, this is why.
- Sub-agent files carry a sibling `agent-*.meta.json` (`agentType`, `description`) — available, deliberately unused so far (YAGNI).
- Cross-worktree aggregation (which dirs to scan, workspace/provenance annotation) is NOT adapter business — it lives in `core/sessions.py` (`SessionExplorer`, see [grove.core](../CLAUDE.md)). Adapters only ever answer for one `cwd`.

## Out-of-band discovery mechanics

**`discover_sessions(cwd, exclude_id)` finds sessions Grove didn't mint** (an agent that wasn't `claude_code` at create, a pre-minting record, or a purely hand-started `claude`). It is a read-only fs glob, so it **always runs** — adapter-gated only (generic/shell ⇒ `[]`, no-op), NOT gated by `cfg.hooks.enabled`. (`hooks.enabled` gates only the push-sidecar install and the augmentation of an already-minted session with concurrent hand-started ones. The recovery *policy* that adopts a discovered session lives in [grove.core](../CLAUDE.md)'s `sessions_for` — it must NOT early-return on a falsy id.)

- **`discover_sessions` returns ids NEWEST-FIRST by transcript mtime, not alphabetical.** Old behavior was `sorted()` — an arbitrary, often-dead pick. The `[:1]` rescue depends on newest-first to land the live session.
- **`_first_cwd` scans PAST the cwd-less preamble** (`mode` / `file-history-snapshot` / `summary` lines) to the first record carrying a `cwd`. Real transcripts open with two-or-three preamble lines before the cwd appears; keying off line 0 alone returns `None` and rejects **every** real transcript. Test fixtures hid this by putting `cwd` on line 0 — **verify discovery against a real on-host transcript, never a hand-built one.**
- **Config-dir cascade:** `CLAUDE_CONFIG_DIR` (CSV) → `~/.config/claude` → `~/.claude`. `projects_dirs` always also appends the latter two.

**Two operational corollaries** that bite even with the code correct:

1. **A custom-named Claude agent MUST declare `kind: "claude_code"`.** Merge-by-name doesn't inherit the built-in `claude`'s kind, so a custom-named entry like `"Claude Code (Custom)"` defaults to `generic`, mints no id, and relies entirely on discovery.
2. **A profile whose command sets `CLAUDE_CONFIG_DIR=~/.other` writes transcripts there**, so the **daemon process** must carry that dir on its own `CLAUDE_CONFIG_DIR` (comma-cascade) or those sessions are invisible.

## Session lessons

- `model.py` (`AgentActivity` / `AgentSession`) is the only thing that crosses outward — keep adapters from leaking native transcript shapes past it.
- Parsing is best-effort like `peek()`: a malformed line skips, it never breaks the render loop. Booleans-as-strings is the recurring footgun.
- When a parsing fact is hard to get right, it's because the real transcript disagrees with the obvious mental model (preamble-before-cwd, user-lines-aren't-turns). Pin it against on-host JSONL, not a fixture.
