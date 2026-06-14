# grove.mcp — the MCP server (stdio workspace control plane)

> ↑ [root](../../../CLAUDE.md)

Exposes the daemon as MCP tools for any MCP client (Mewbo, Claude Code, Claude Desktop). Phase 1 surface of Gitea issues #1 (architecture) and #6 (package). Requires the `[mcp]` extra (official `mcp` SDK).

## The boundary rule (non-negotiable)

- **MCP client → `grove.mcp` → `GroveClient` → daemon REST → core. Never import engine internals** (manager/store/git/tmux/registry/config); the daemon is the single authority, which is also what lets this server run on a different host than the engine. Enforced by the "MCP server speaks only through the client SDK" import-linter contract (direct imports only — the client/contracts legitimately reach core modules underneath).
- Connects via the client SDK's `UrlTransport` (`BackendConfig.daemon_url`) — attach to an externally supervised daemon, never spawn one. Auth: `GROVE_API_TOKEN` wins if set; otherwise `GroveClient` mints a same-host session from `auth.json`, so a co-located daemon needs zero setup.

## Tool surface (published contract — renaming breaks every configured client)

`grove_list_workspaces` · `grove_get_workspace` · `grove_create_workspace` · `grove_peek_workspace` · `grove_pause_workspace` · `grove_resume_workspace` · `grove_respawn_workspace` · `grove_kill_workspace` · `grove_attach_instruction` · `grove_send_workspace_message`

- Outputs reuse the contract Views; `models.py` adds shapes only where the daemon returns no body (kill → 204) or where availability itself is the answer (message). Explicit status fields, never prose an agent must parse.
- **`kill` takes a required `delete_branch`** — destructive tools never guess; schema validation rejects an omitted flag before the client is ever called (pinned by test).
- Peek pane snapshots cap at `GroveTools.SNAPSHOT_CAP` (4 KB, trailing ellipsis = trim signal), mirroring the contracts package's bounded-text rule.
- `send_workspace_message` targets daemon issue #37's `POST /workspaces/{id}/message`. A bare 404/405 (`ProtocolError code="http_error"`) means the route is absent → `status="unavailable"` (capability discovery per issue #1's addendum); an enveloped `workspace_not_found` is a real caller error and propagates.

## Structure

`server.py` = env config (`McpServerConfig`) + FastMCP wiring + lifespan-owned `GroveClient`; `tools.py` = `GroveTools`, the seam tests pin (handlers in, Views out, fake client at the HTTP boundary); `models.py` = the few result shapes. Tool method **docstrings ARE the MCP descriptions** an agent reads when choosing tools — keep them action-first and explicit about side effects.

## Session lessons

- **FastMCP (official `mcp` SDK, locked 1.27.2 on 2026-06-11) accepts bound methods in `add_tool`** and derives schemas from signatures, including the `BranchPlan` Pydantic discriminated union. `call_tool` returns `(content, structured)`; a list return is wrapped under `{"result": [...]}` in the structured half.
- **The stdio run enters the FastMCP lifespan**, so connect the `GroveClient` there once for the whole session — the local token mint writes `auth.json` and is not free per call.
- A function-call default like `branch_plan: BranchPlan = AutoBranch()` trips ruff B008; share one module-level frozen instance instead.
- **`grove-mcp` is an unconditional console script, but the `mcp` SDK lives behind the opt-in `[mcp]` extra** — a `.[daemon]`-only host has the script on PATH with no SDK. So the FastMCP import is **deferred** (`_load_fastmcp`, called from `GroveMcpServer.__init__`, guarded by `TYPE_CHECKING` for the annotation): a top-level import would crash the whole `grove.mcp` package at load time (before `main()` runs) with a chained traceback the MCP client buries. `main()` catches the `ImportError` and emits the install hint to stderr + `sys.exit(1)` — clean hint, no traceback (#44).
