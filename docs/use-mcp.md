# MCP Server

## Let an agent drive Grove

Grove ships an MCP server, `grove-mcp`: a radio to the daemon's control tower. An assistant tunes in and can list, create, steer, and tear down workspaces without touching git or tmux, through the same daemon API the TUI and dashboard use. Grove's safety rules apply unchanged. Your git stays yours.

---

## Choosing a range

Both transports serve the identical tools, differing in who starts the process and who may reach it.

| | stdio | Streamable HTTP |
|---|---|---|
| Who starts it | Your client, per connection. | You do. It keeps running. |
| Who reaches it | That client, on this machine. | Anything reaching the port. |
| What authenticates | The process boundary. | A bearer token you set. |
| What it costs | Nothing. | A token, a port, a safe path. |
| Best for | Your laptop. | A remote or hosted harness. |

Start with stdio. HTTP adds a port, an inbound token, and safe routing. Both reach the same daemon, so run one of each if needed.

---

## Same machine: stdio

The client spawns the process: no token or port to protect. Install the `mcp` extra and start the daemon:

```bash
pip install 'grove[all]'   # or 'grove[mcp]' for just the MCP server
grove daemon serve         # or run the packaged systemd user service
```

Register it with your client:

```json title="mcp.json"
{
  "mcpServers": {
    "grove": {
      "command": "grove-mcp",
      "env": {
        "GROVE_API_URL": "http://127.0.0.1:7421"
      }
    }
  }
}
```

Claude Code does it in one command:

```bash
claude mcp add grove -- grove-mcp
```

`grove mcp install` writes that registration through each harness's own `mcp add`, for Claude Code and Codex over stdio.

---

## Another machine: HTTP

Pass `--transport streamable-http` and `grove-mcp` faces the network instead, reaching the daemon over loopback or an SSH forward. The daemon itself still binds loopback, per the [security model](use-auth.md#the-security-model):

```mermaid
flowchart TB
    Harness(["Remote harness"])
    MCP(["grove-mcp<br/>:7431"])
    Daemon(["grove-daemon<br/>:7421"])
    Harness -->|"HTTP + Bearer, wrap in TLS or a tunnel yourself"| MCP
    MCP -->|loopback| Daemon
```

Start it:

```bash
export GROVE_MCP_TOKEN="$(openssl rand -hex 32)"
grove-mcp --transport streamable-http --host 127.0.0.1 --port 7431
```

`http` is an alias for `streamable-http`, Claude Code's own name for it. The endpoint mounts at `/mcp` unless `--path` moves it, printed to stderr on startup along with its scope.

### Two tokens, two directions

Two credentials point in opposite directions, never the same string.

| Token | Direction | Required |
|---|---|---|
| `GROVE_API_TOKEN` | Outbound. This server to the daemon. | Optional. Omit it on the daemon's own host and a local session is minted from `auth.json`, like the TUI. |
| `GROVE_MCP_TOKEN` | Inbound. A remote harness to this server. | Mandatory for a network transport. |

Callers present the inbound token as `Authorization: Bearer <GROVE_MCP_TOKEN>`.

> [!WARNING] The inbound token is not optional
> An open port hands full workspace lifecycle control to anyone reaching it, `grove_kill_workspace` included, so `grove-mcp` fails closed: without `GROVE_MCP_TOKEN` it refuses to bind, exits `2`, and prints the fix. There is deliberately no `--insecure` escape hatch, since an opt-out flag survives into production.

### Connecting a client

One command registers it:

```bash
claude mcp add --transport http grove http://<host>:7431/mcp \
  --header "Authorization: Bearer $GROVE_MCP_TOKEN"
```

That writes this entry:

```json title="mcp.json"
{
  "mcpServers": {
    "grove": {
      "type": "http",
      "url": "http://<host>:7431/mcp",
      "headers": {
        "Authorization": "Bearer ${GROVE_MCP_TOKEN}"
      }
    }
  }
}
```

Claude Code interpolates `${VAR}` in headers, keeping tokens out of the file. A static bearer header wins over OAuth.

### Where it is safe to run

The transport is plain HTTP, so the bearer token crosses in cleartext: fine on a trusted LAN, WireGuard, Tailscale, or behind a TLS-terminating proxy, never the open internet. Bind loopback and forward the port instead, the lever the [dashboard](use-webapp.md#reaching-it-from-outside-the-network) uses:

```bash
ssh -N -L 7431:127.0.0.1:7431 you@grove-host.internal
```

The harness then points at `http://127.0.0.1:7431/mcp` locally, reaching for `--host 0.0.0.0` only once a VPN or TLS proxy fronts it.

`GROVE_MCP_ALLOWED_HOSTS` and `GROVE_MCP_ALLOWED_ORIGINS` each take a comma-separated list, switching on DNS-rebinding protection, off by default: only named `Host` headers are accepted, so a page on another domain cannot rebind onto your port.

### Read-only exposure

`--read-only` (or `GROVE_MCP_READ_ONLY=1`) registers only the tools that observe, marked in the [tool table](#tools): a tool an agent can see is one it will eventually try, so withheld tools leave the surface rather than being refused at call time. Works at either range.

---

## Hosting it as a service

A stdio server needs no supervision, but an HTTP one is long-lived, so Grove ships an opt-in systemd unit:

```bash
WITH_MCP=1 make systemd
WITH_MCP=1 make systemd-enable
```

That renders `grove-mcp.service`, bound to `127.0.0.1:7431` unless `MCP_HOST`/`MCP_PORT` override it, reading its token from a file rather than baking a secret into the unit:

```bash
install -m 600 /dev/null ~/.config/grove/mcp.env
echo "GROVE_MCP_TOKEN=$(openssl rand -hex 32)" >> ~/.config/grove/mcp.env
```

Missing that file, the service starts and exits `2` with the reason in `journalctl --user -u grove-mcp -f`: fail-closed working, not a packaging bug.

> [!TIP]
> A hosted HTTP server needs a restart after a Grove upgrade, like the daemon. A stdio server needs none: your client respawns it.

---

## Configuration

A CLI flag wins, the environment comes next, and a built-in default fills the gap.

### Talking to the daemon (outbound)

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_API_URL` (or `--api-url`) | `http://127.0.0.1:7421` | Base URL of the daemon. |
| `GROVE_API_TOKEN` | unset | Bearer token for the daemon. Unset on its own host, where a local session is minted. Set it when the URL points elsewhere. Pair once, then export it. |

### Serving callers (inbound)

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_MCP_TRANSPORT` (or `--transport`) | `stdio` | `stdio` or `streamable-http`. |
| `GROVE_MCP_HOST` (or `--host`) | `127.0.0.1` | Interface to bind. |
| `GROVE_MCP_PORT` (or `--port`) | `7431` | Port to bind, adjacent to the daemon's `7421`. |
| `GROVE_MCP_PATH` (or `--path`) | `/mcp` | Where the endpoint mounts. |
| `GROVE_MCP_TOKEN` | unset | Inbound bearer token. Mandatory under a network transport. |
| `GROVE_MCP_READ_ONLY` (or `--read-only`) | off | Register only the non-mutating tools. |
| `GROVE_MCP_ALLOWED_HOSTS` | unset | `Host` allowlist. Enables DNS-rebinding protection. |
| `GROVE_MCP_ALLOWED_ORIGINS` | unset | `Origin` allowlist, same switch. |

All but `GROVE_MCP_READ_ONLY` are inert under stdio, which binds no socket and authenticates no caller. Read-only shapes registration instead, applies at either range, and can only tighten it: an environment variable that set it survives an omitted `--read-only`.

### Which file holds what

| File | Owns | Location |
|---|---|---|
| Your client's MCP registration | How a client reaches Grove. The command for stdio, URL and headers for HTTP. | `.mcp.json`, or the client's user config |
| `mcp.env` | Tokens a hosted server starts with, read by the systemd unit. | `~/.config/grove/mcp.env`, mode `600` |
| The systemd unit | How the server is supervised, and what it binds. | `~/.config/systemd/user/grove-mcp.service` |
| Grove's own config | Everything the *daemon* does: projects, agents, worktrees, tickets. | `~/.config/grove/config.json` and `.grove/config.json` |

> [!NOTE]
> Grove's [config cascade](configure-reference.md) carries no `mcp` section, on purpose: the server is a client of the daemon, not part of it, and runs on a machine with no Grove config at all. It reads only flags and environment variables, kept in `mcp.env` for a hosted server.

The cascade still decides which projects exist, which agents `grove_list_agents` offers, and what `grove_create_workspace` builds: configure the fleet there, the radio here.

---

## Tools

A ✓ marks a tool that survives `--read-only`.

| Tool | What it does | RO |
|---|---|---|
| `grove_list_projects` | Every configured project. No arguments, so it is where an agent holding no path starts. Rows carry `repo_root` (what repo-scoped tools take), `repo_name`, `cwd`. | ✓ |
| `grove_list_workspaces` | Every workspace with id, branch, agent, status. | ✓ |
| `grove_get_workspace` | Full state for one workspace by id. | ✓ |
| `grove_get_fleet_status` | Every workspace at once: state, phase, todo and git counts, and per session the agent's activity, `needs_attention`, task, question, tokens. The only read that reports an agent waiting on you. | ✓ |
| `grove_list_agents` | Agents for a repo with their `models` catalog. The valid `agent_name` and `model` for `grove_create_workspace`. | ✓ |
| `grove_list_sessions` | Agent sessions on this host, newest first, including ones Grove never launched. `repo_root` narrows to a project and fills the otherwise-null `activity`, `size_bytes` and prompt fields. | ✓ |
| `grove_peek_workspace` | Bounded snapshot: ahead/behind, diff stats, dirty files, commits, capped pane output. | ✓ |
| `grove_get_workspace_phase` | The reported [task phase](features-status.md#the-third-axis-task-phase) and note. Null means none reported, unlike a reported `scoping`. | ✓ |
| `grove_get_workspace_todo` | The todo list parsed from the transcript (`TodoWrite`, `update_plan`, Mewbo's board), each item with its state. | ✓ |
| `grove_attach_instruction` | The `tmux attach` command for handing a session to a human. | ✓ |
| `grove_create_workspace` | Worktree, branch, tmux session, agent. Params: `repo_root`, `title`, `agent_name`, optional `description`, `branch_plan` (`auto` default, `new_named`, `existing_local`, `track_remote`, `root`), `skip_init`, `initial_prompt`, `resume_session_id`, `model`. `initial_prompt` delivers the first task race-free at boot, and `model` is forwarded verbatim. | |
| `grove_pause_workspace` | Drop worktree and session, keep the branch. Refuses a dirty worktree unless `force`. | |
| `grove_resume_workspace` | Recreate a paused workspace from its branch. | |
| `grove_respawn_workspace` | Recreate a vanished tmux session. | |
| `grove_kill_workspace` | Destroy a workspace. `delete_branch` is required with no default. Remote branches are never touched. | |
| `grove_send_workspace_message` | Steer the agent. Returns `status="sent"`, or `status="unavailable"` on a daemon predating the endpoint. | |
| `grove_remap_workspace_session` | Re-point a workspace at another session after `/clear` rotated the id. Params: `workspace_id`, `session_ref` (full id or unique prefix). | |
| `grove_attach_ticket` | Attach an issue or PR. Params: `workspace_id`, `ref` (a URL, `#42`, `42`, or `owner/repo#42`). Provider and issue-vs-PR are inferred, and an ambiguous id raises. Idempotent. See [ticket providers](features-ticket-providers.md). | |
| `grove_detach_ticket` | Remove that association. Same params. Idempotent. | |
| `grove_set_workspace_phase` | Set the phase. Params: `workspace_id`, `phase` (`scoping`, `planning`, `implementing`, `verifying`, `delivering`, `done`), optional `note`. | |

Every response is structured JSON with explicit status fields and stable workspace ids.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools: create, list, peek, pause, and steer workspaces" /></div>
  <figcaption class="ms-shot__body">The Grove MCP tools as seen from Claude Code.</figcaption>
</figure>

---

## Using Grove from Mewbo

Mewbo can adopt Grove as its durable workspace backend, its hypervisor calling the tools above while Grove stays the system of record for worktrees, branches, sessions, and lifecycle state. Grove owns the control plane, Mewbo owns scheduling, skills, and conversation, pointing its MCP pool at either range.

To run Mewbo, or any agent, inside a workspace, define it as a regular agent in your project config:

```json title=".grove/config.json"
{
  "agents": [
    {
      "name": "mewbo",
      "command": "uv run mewbo",
      "description": "Mewbo assistant in this Grove worktree"
    }
  ]
}
```

See [Agents](configure-agents.md) for the config reference, and [Workspace Lifecycle](features-workspace-lifecycle.md) for what pause, respawn, and kill mean.
