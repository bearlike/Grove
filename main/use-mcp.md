# MCP Server

Grove ships an MCP server: `grove-mcp`. It exposes your workspace fleet as tools that any MCP-capable agent can call. Think of the Grove daemon as a control tower and `grove-mcp` as its radio: an assistant like Mewbo or Claude tunes in and can list, create, steer, and tear down workspaces without touching git or tmux itself.

The radio works at two ranges. Over stdio your client spawns `grove-mcp` as a child process, which is all you need when the agent runs beside you. Over Streamable HTTP the same server binds a port behind a bearer token, so a harness on another machine tunes into the same frequency.

The server is a thin adapter at either range. Every tool call travels through the same daemon REST API the TUI and web dashboard use, so all of Grove's safety rules apply unchanged: your git stays yours.

---

## Choosing a range

Both transports serve the identical twelve tools. They differ only in who starts the process and who is allowed to talk to it.

| | stdio | Streamable HTTP |
|---|---|---|
| Who starts it | Your MCP client, once per connection. | You do. It stays running. |
| Who can reach it | Only that client, on this machine. | Anything that can reach the port. |
| What authenticates | The operating system process boundary. | A bearer token you set. |
| What it costs to set up | Nothing. | A token, a port, and a safe path to it. |
| Best for | Your own laptop. | A remote harness, a shared fleet view, a hosted assistant. |

Start with stdio. It is the default, it needs no secrets, and it covers the common case where you and your agent sit on the same machine.

Reach for HTTP when the agent does not. That covers a hosted assistant, a teammate's machine, a CI runner, or a dashboard-style harness that only watches. Three things change when you cross that line. You choose a port. You set an inbound token. You decide how the traffic gets there safely.

Nothing forces you to pick one forever. Both transports talk to the same daemon, so you can register a stdio server for yourself and run an HTTP one alongside it for everything else.

---

## Same machine: stdio

Your client spawns the process, so the operating system's process boundary is the authentication. There is no token to manage and no port to protect.

Install Grove with the `mcp` extra and make sure the daemon is running:

```bash
pip install 'grove[all]'   # or 'grove[mcp]' for just the MCP server
grove daemon serve         # or run the packaged systemd user service
```

Then register the server with your MCP client. A generic `mcp.json` entry:

```json
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

For Claude Code, one command does the same:

```bash
claude mcp add grove -- grove-mcp
```

`grove mcp install` writes that registration through each harness's own `mcp add`, so you do not have to hand-edit a file per client. It covers Claude Code and Codex. Codex reaches Grove over stdio.

---

## Another machine: HTTP

Pass `--transport streamable-http` and `grove-mcp` stops being a child process and becomes a server.

The daemon does not move. It still binds loopback, exactly as the [security model](use-auth.md#the-security-model) describes. `grove-mcp` is the process that faces the network. It reaches the daemon over loopback on its own host, or over an SSH forward if you run the two apart:

```text
remote harness  --HTTP + Bearer-->  grove-mcp :7431  --loopback-->  grove-daemon :7421
                 (wrap in TLS or a
                  tunnel yourself)
```

Start it:

```bash
export GROVE_MCP_TOKEN="$(openssl rand -hex 32)"
grove-mcp --transport streamable-http --host 127.0.0.1 --port 7431
```

`http` is an alias for `streamable-http`, so `--transport http` means the same thing and matches what Claude Code calls it. The endpoint mounts at `/mcp` unless `--path` moves it. On startup the server prints its resolved address and scope to stderr, so one glance confirms what you published.

### Two tokens, two directions

The one thing to get right is which credential goes where. Grove uses two of them. They point in opposite directions. They are never the same string.

| Token | Direction | Required |
|---|---|---|
| `GROVE_API_TOKEN` | Outbound. This server to the Grove daemon. | Optional. Omit it on the daemon's own host and the server mints a local session from `auth.json`, the same way the TUI does. |
| `GROVE_MCP_TOKEN` | Inbound. A remote harness to this server. | Mandatory for a network transport. |

Callers present the inbound token as `Authorization: Bearer <GROVE_MCP_TOKEN>`.

> [!WARNING] The inbound token is not optional
> An open port here would hand full workspace lifecycle control to anyone who can reach it. That includes `grove_kill_workspace`. So `grove-mcp` fails closed. Without `GROVE_MCP_TOKEN` it refuses to bind, exits `2`, and prints the fix. There is deliberately no `--insecure` escape hatch. Setting a token is one environment variable, and an opt-out flag is the kind of thing that survives into production.

### Connecting a client

For Claude Code, one command registers the remote server:

```bash
claude mcp add --transport http grove http://<host>:7431/mcp \
  --header "Authorization: Bearer $GROVE_MCP_TOKEN"
```

That writes an entry you can also keep by hand:

```json
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

Claude Code interpolates `${VAR}` in headers, so the token lives in your environment and never in the committed file. A static bearer header also takes precedence over any OAuth flow, so the client uses the token you gave it instead of trying to negotiate one.

### Where it is safe to run

The transport is plain HTTP carrying a bearer token. Read that literally: the token crosses the wire in cleartext. That is fine on a trusted LAN, across a WireGuard or Tailscale network, or behind a TLS-terminating reverse proxy. It is not fine on the open internet.

So bind loopback and forward the port, the same lever the [dashboard](use-webapp.md#reaching-it-from-outside-the-network) uses:

```bash
ssh -N -L 7431:127.0.0.1:7431 you@grove-host.internal
```

The harness then points at `http://127.0.0.1:7431/mcp` on its own machine. Reach for `--host 0.0.0.0` only once a VPN or a TLS-terminating proxy sits in front of it.

Two optional allowlists harden a bound port further. `GROVE_MCP_ALLOWED_HOSTS` and `GROVE_MCP_ALLOWED_ORIGINS` each take a comma-separated list and switch on DNS-rebinding protection, which is off until you set one. Once set, only the `Host` headers you named are accepted, so a page served from some other domain cannot rebind its way onto your port.

### Read-only exposure

`--read-only` (or `GROVE_MCP_READ_ONLY=1`) registers only the tools that observe. It withholds `grove_create_workspace`, `grove_pause_workspace`, `grove_resume_workspace`, `grove_respawn_workspace`, `grove_kill_workspace`, `grove_send_workspace_message`, `grove_remap_workspace_session`, `grove_attach_ticket`, and `grove_detach_ticket`, leaving `grove_list_projects`, `grove_list_workspaces`, `grove_get_workspace`, `grove_list_agents`, `grove_peek_workspace`, and `grove_attach_instruction`.

Withholding is the point. A tool an agent can see is a tool it will eventually try, so the read-only scope removes those tools from the surface rather than refusing them at call time. Use it to give a monitoring or reporting harness a live view of the fleet without handing it lifecycle control.

This one is not tied to the network. Registration happens the same way at either range, so `--read-only` works over stdio too. Reach for it whenever you want a local agent that reports on the fleet without being able to change it.

> [!TIP]
> Run two servers when you want both. Keep a full-control instance for your own harness. Expose a read-only one on a second port to anything you trust less. They share one daemon and cost you nothing but a second process.

---

## Hosting it as a service

A stdio server needs no supervision, because the client starts and stops it. An HTTP server is long-lived, so it wants the same treatment the daemon gets.

Grove ships a systemd user unit for exactly this. It is opt-in, because most people never need it:

```bash
WITH_MCP=1 make systemd
WITH_MCP=1 make systemd-enable
```

That renders `grove-mcp.service`, pointed at `127.0.0.1:7431` by default. Override the bind at install time with `MCP_HOST` and `MCP_PORT`.

The unit reads its token from a file rather than baking a secret into the unit text:

```bash
install -m 600 /dev/null ~/.config/grove/mcp.env
echo "GROVE_MCP_TOKEN=$(openssl rand -hex 32)" >> ~/.config/grove/mcp.env
```

If that file is missing, the service starts and then exits `2` with the reason in its log. That is the fail-closed behavior working as intended, not a packaging bug. Read the log with `journalctl --user -u grove-mcp -f`.

> [!TIP]
> A hosted HTTP server is a long-lived process, so it does not pick up a Grove upgrade on its own. Restart it after an update, the same as the daemon. A stdio server needs no restart, because your client respawns it on the next connection.

---

## Configuration

`grove-mcp` resolves each setting in one order. A CLI flag wins. The environment comes next. A built-in default fills the gap.

### Talking to the daemon (outbound)

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_API_URL` (or `--api-url`) | `http://127.0.0.1:7421` | Base URL of the running Grove daemon. |
| `GROVE_API_TOKEN` | unset | Bearer token for the daemon. Leave it unset on the daemon's own host. The server mints a local session automatically, the same way the TUI does. Set it when the URL points at another machine. Pair once, then export the token. |

### Serving callers (inbound)

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_MCP_TRANSPORT` (or `--transport`) | `stdio` | `stdio` or `streamable-http`. `http` is an alias for the latter. |
| `GROVE_MCP_HOST` (or `--host`) | `127.0.0.1` | Interface to bind under a network transport. |
| `GROVE_MCP_PORT` (or `--port`) | `7431` | Port to bind. Adjacent to the daemon's `7421`, so the two Grove ports read as a pair. |
| `GROVE_MCP_PATH` (or `--path`) | `/mcp` | Path the endpoint mounts at. |
| `GROVE_MCP_TOKEN` | unset | The inbound bearer token callers must present. Mandatory under a network transport. |
| `GROVE_MCP_READ_ONLY` (or `--read-only`) | off | Register only the non-mutating tools. |
| `GROVE_MCP_ALLOWED_HOSTS` | unset | Comma-separated `Host` allowlist. Setting it enables DNS-rebinding protection. |
| `GROVE_MCP_ALLOWED_ORIGINS` | unset | Comma-separated `Origin` allowlist, behind the same switch. |

The binding and authentication settings are inert under stdio. There is no socket to bind and no caller to authenticate, so `GROVE_MCP_HOST`, `GROVE_MCP_PORT`, `GROVE_MCP_PATH`, `GROVE_MCP_TOKEN`, and the two allowlists simply go unused.

`GROVE_MCP_READ_ONLY` is the exception. It shapes which tools get registered, which is true of any transport, so it works over stdio as well. One asymmetry there is deliberate. `--read-only` can only tighten. If the environment already set it, omitting the flag will not turn it back off.

### Which file holds what

Four files can shape an MCP setup, and each owns a different job. Keeping them straight saves a lot of hunting.

| File | Owns | Typical location |
|---|---|---|
| Your client's MCP registration | How a client reaches Grove. The command for stdio, or the URL and headers for HTTP. | `.mcp.json` in a project, or the client's user-level config |
| `mcp.env` | The tokens and knobs a hosted server starts with. Read by the systemd unit. | `~/.config/grove/mcp.env`, mode `600` |
| The systemd unit | How the server is supervised, and which host and port it binds. | `~/.config/systemd/user/grove-mcp.service` |
| Grove's own config | Everything the *daemon* does. Projects, agents, worktrees, tickets, notifications. | `~/.config/grove/config.json` and `.grove/config.json` |

> [!NOTE]
> Grove's [config cascade](configure-reference.md) does not carry an `mcp` section, and that is on purpose. The MCP server is a client of the daemon rather than part of it, and it is supported on a machine where no Grove config file exists at all. So it reads flags and environment variables only. For a hosted server, `mcp.env` is the durable place to write those down.

The cascade still matters indirectly. It decides which projects exist, which agents `grove_list_agents` offers, and what `grove_create_workspace` builds. Configure the fleet there. Configure the radio here.

---

## Tools

| Tool | What it does |
|---|---|
| `grove_list_projects` | List every project Grove is configured to work in. Takes no arguments, so it is the place to start when the agent holds no path yet. Each row carries `repo_root` (the value every repo-scoped tool below expects), `repo_name` for display, and `cwd`, which differs from `repo_root` only for a project nested inside a larger repo. Read-only. |
| `grove_list_workspaces` | List every workspace with id, branch, agent, and status. |
| `grove_get_workspace` | Full state for one workspace by id. |
| `grove_list_agents` | List the agents available for a repo, each with its offered `models` catalog (up to 10). These are the valid `agent_name` and `model` values for `grove_create_workspace`. Read-only. |
| `grove_create_workspace` | Create a workspace: worktree, branch, tmux session, agent. Params: `repo_root`, `title`, `agent_name`, optional `description`, `branch_plan` (`auto` default, `new_named`, `existing_local`, `track_remote`, `root`), `skip_init`, `initial_prompt`, `resume_session_id`, `model`. Pass `initial_prompt` to deliver the agent's first task race-free at boot so the workspace starts working immediately; omit it to boot idle and steer later with `grove_send_workspace_message`. `model` is forwarded verbatim to the agent tool (claude/codex as `--model <id>`, mewbo server-side; generic ignores it). Any id the tool understands is accepted, not just a fixed list. Call `grove_list_agents` first to see the offered catalog as a hint. |
| `grove_peek_workspace` | Bounded snapshot: ahead/behind, diff stats, dirty files, recent commits, capped pane output. |
| `grove_pause_workspace` | Remove worktree and session, keep the branch. Refuses dirty worktrees unless `force` is true. |
| `grove_resume_workspace` | Recreate a paused workspace from its branch. |
| `grove_respawn_workspace` | Recreate a vanished tmux session for an offline workspace. |
| `grove_kill_workspace` | Destroy a workspace. `delete_branch` is required and has no default: the caller must state intent. Remote branches are never touched. |
| `grove_attach_instruction` | The `tmux attach` command for handing a session to a human. |
| `grove_send_workspace_message` | Send a steering message to the workspace's agent. Returns `status="sent"` on success or `status="unavailable"` when the connected daemon predates the messaging endpoint. |
| `grove_remap_workspace_session` | Re-point a workspace to a different agent session. Use it after `/clear` rotated the session id, or to adopt a hand-started session as the workspace's own. Params: `workspace_id`, `session_ref` (a full session id or a unique id-prefix within the workspace's project). |
| `grove_attach_ticket` | Attach an issue or pull request to a workspace — the one-call verb for an agent that just opened a PR and wants to link it to the workspace that made it. Params: `workspace_id`, `ref` (a pasted URL, `#42`, `42`, or `owner/repo#42`). Provider and issue-vs-PR are both inferred from `ref`; an id that could belong to more than one enabled tracker raises rather than guessing. Idempotent. See [ticket providers](features-ticket-providers.md). |
| `grove_detach_ticket` | Remove an issue/PR association from a workspace. Params: `workspace_id`, `ref`, accepting the same shapes as `grove_attach_ticket`. Idempotent. |
| `grove_set_workspace_phase` | Set a workspace's [task phase](features-status.md#the-third-axis-task-phase). Params: `workspace_id`, `phase` (one of `scoping`, `planning`, `implementing`, `verifying`, `delivering`, `done`), optional `note`. Writes the same per-agent phase file an agent would write itself. |
| `grove_get_workspace_phase` | Read a workspace's current task phase and note, plus when it was last reported. |
| `grove_get_workspace_todo` | Read the agent's todo list as Grove parsed it from the transcript (Claude's `TodoWrite`, Codex's `update_plan`, Mewbo's task board), each item with its done/in-progress/pending state. |

Every response is structured JSON with explicit status fields and stable workspace ids, so a calling agent never has to parse prose to learn what happened.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools: create, list, peek, pause, and steer workspaces" /></div>
  <figcaption class="ms-shot__body">The Grove MCP tools as seen from Claude Code. Create, list, peek, pause, resume, kill, and steer workspaces through a single connected server.</figcaption>
</figure>

---

## Using Grove from Mewbo

Mewbo can adopt Grove as its durable workspace backend over this server. Mewbo's hypervisor calls the tools above. Grove stays the system of record for worktrees, branches, sessions, and lifecycle state. The division of responsibility is deliberate. Grove owns the workspace control plane. Mewbo owns scheduling, skills, and conversation.

Point Mewbo's MCP pool at whichever range fits. Use stdio when it runs on the same host, and the HTTP endpoint when it does not. Either way it discovers the tool surface automatically.

To run Mewbo (or any agent) inside Grove workspaces, define it as a regular agent in your project config, for example:

```json
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

See [Agents](configure-agents.md) for the full agent configuration reference, and [Workspace Lifecycle](features-workspace-lifecycle.md) for what pause, respawn, and kill mean in detail.
