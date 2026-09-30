# src

The `grove` Python package: engine, daemon, clients and skills. Each package is
one CI component, so a change runs only its own jobs plus those of the packages
that import it.

```
src/grove/
├── core/            engine: lifecycle, manager, config cascade, store, status — CI: core
│   ├── contracts/       wire-level Pydantic shapes that cross clients
│   ├── agents/          tool-agnostic agent introspection (adapters)
│   ├── tickets/         branch-aware ticket providers
│   ├── issueops/        issue-comment events → workspace actions
│   ├── telemetry/       OpenTelemetry gateway
│   ├── notifications/   push notifications on workspace edges
│   ├── usage/           tokens, time, cost, quota (derived SQLite cache)
│   └── watches/         durable callbacks over the mailbox
├── daemon/          loopback FastAPI daemon — CI: daemon
├── client/          transport-agnostic attach (local PTY / SSH) — CI: client
├── mcp/             MCP server over the client SDK — CI: mcp
├── tui/             Textual terminal UI and the `grove` CLI commands — CI: tui
├── skills/          published agent skills (one copy; see root CLAUDE.md) — CI: core
└── *.py             entry points and small shared modules — CI: core
```

`import-linter` enforces the direction of imports: `core` imports no UI, the
daemon only `core`, the client neither daemon nor TUI, and MCP only the client
SDK. Those same edges are the `dependents` in
[`.github/ci/components.json`](../.github/ci/components.json).

How each package is built lives in its own `CLAUDE.md`, listed in the
[root guide](../CLAUDE.md#project-structure).
