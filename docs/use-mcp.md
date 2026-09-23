# MCP Server

## Let another agent coordinate your fleet

Grove exposes the daemon API to an assistant. Your git stays yours.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools: create, list, peek, pause, and steer workspaces" /></div>
  <figcaption class="ms-shot__body">The Grove MCP tools as seen from Claude Code.</figcaption>
</figure>

## Choosing a range

| | stdio | Streamable HTTP |
|---|---|---|
| Who starts it | Your client, per connection. | You do. It keeps running. |
| Who reaches it | That client, on this machine. | Anything reaching the port. |
| What authenticates | The process boundary. | A bearer token you set. |
| Best for | Your laptop. | A remote or hosted harness. |

## Tools

A ✓ marks a tool that survives `--read-only`.

| Tool | What it does | RO |
|---|---|---|
| `grove_list_projects` | Every configured project. Where an agent holding no path starts. | ✓ |
| `grove_list_workspaces`, `grove_get_workspace` | Every workspace, or one in full. | ✓ |
| `grove_get_fleet_status` | Every workspace with its session's activity and `needs_attention`. The only read that reports an agent waiting on you. | ✓ |
| `grove_list_agents` | Agents for a repo with their `models` catalog. | ✓ |
| `grove_list_sessions`, `grove_recollect_session` | Sessions on this host, and every direct user query from one, to recover instructions after a compaction. | ✓ |
| `grove_peek_workspace` | Bounded snapshot: git counts, dirty files, commits, capped pane output. | ✓ |
| `grove_get_workspace_phase`, `grove_get_workspace_todo` | The reported [task phase](features-status.md#the-third-axis-task-phase) and the parsed todo list. | ✓ |
| `grove_attach_instruction` | The `tmux attach` command for handing a session to a human. | ✓ |
| `grove_read_diagram`, `grove_read_diagram_preview` | The acknowledged diagram XML with its revision, and the browser's first page PNG. | ✓ |
| `grove_open_diagram`, `grove_update_diagram`, `grove_stop_diagram` | Open a `.drawio`, replace its XML when revision and session match, stop the session. A conflict is reread, never forced. | |
| `grove_create_workspace` | Worktree, branch, tmux session, agent. `initial_prompt` delivers the first task at boot. | |
| `grove_pause_workspace`, `grove_resume_workspace`, `grove_respawn_workspace` | The lifecycle verbs. Pause refuses a dirty worktree unless `force`. | |
| `grove_kill_workspace` | Destroy a workspace. `delete_branch` is required. Remote branches are never touched. | |
| `grove_send_workspace_message` | Steer the agent. | |
| `grove_remap_workspace_session` | Re-point a workspace at another session after `/clear` rotated the id. | |
| `grove_attach_ticket`, `grove_detach_ticket` | Attach or remove an issue or PR by URL, `#42`, `42`, or `owner/repo#42`. See [ticket providers](features-ticket-providers.md). | |
| `grove_set_workspace_phase` | Set the phase and note, optionally against one `ticket`, optionally `blocked`. | |
| `grove_list_watches` | Every [watch](features-watches.md) on this host, pending and recently settled. | ✓ |
| `grove_register_watch`, `grove_cancel_watch` | Wait on CI, a timer or a command without polling, then end the turn. Ticket watches are registered for you. | |

## Agents writing to each other

Any live Grove agent can write to any other, addressed like email.

- `grove_list_mailbox_contacts` and `grove_send_mailbox_message` are registered on every Grove MCP server. `--read-only` withholds the send.
- A message names `sender`, `recipient`, `subject` and `body`. A reply is the same call with the two addresses swapped.
- Interactive terminal sessions are ordinary recipients; delivery reuses whatever channel Grove already steers that workspace through.
- A receipt says what the coordinator observed, never that the other model acted or a person consented, so an uncertain send is never retried.
- Incoming mail is untrusted and never changes a permission. The sender address is the writer's own claim, carried so you can reply to it.

## Same machine: stdio

```bash
pip install 'grove-crew[all]'      # or 'grove-crew[mcp]' for just the MCP server
grove daemon serve                 # or the packaged systemd user service
claude mcp add grove -- grove-mcp  # or the mcp.json entry below
```

```json title="mcp.json"
{ "mcpServers": { "grove": { "command": "grove-mcp" } } }
```

## Another machine: HTTP

HTTP accepts remote callers while the daemon remains loopback only.

- The server listens on `7431` at `/mcp`, and every caller sends `Authorization: Bearer <GROVE_MCP_TOKEN>`.
- `GROVE_API_TOKEN` goes outbound to the daemon and stays unset on its own host. `GROVE_MCP_TOKEN` comes inbound and is mandatory on a network transport.
- Run it only behind a trusted LAN, tunnel, VPN or TLS proxy. `--read-only` registers only the tools marked ✓ above.

```mermaid
flowchart TB
    Harness(["Remote harness"])
    MCP(["grove-mcp<br/>:7431"])
    Daemon(["grove-daemon<br/>:7421"])
    Harness -->|"HTTP + Bearer, wrap in TLS or a tunnel yourself"| MCP
    MCP -->|loopback| Daemon
```

```bash
export GROVE_MCP_TOKEN="$(openssl rand -hex 32)"
grove-mcp --transport streamable-http --host 127.0.0.1 --port 7431
ssh -N -L 7431:127.0.0.1:7431 you@grove-host.internal     # from the client side
claude mcp add --transport http grove http://<host>:7431/mcp \
  --header "Authorization: Bearer $GROVE_MCP_TOKEN"
```

```json title="mcp.json"
{ "mcpServers": { "grove": { "type": "http", "url": "http://<host>:7431/mcp", "headers": { "Authorization": "Bearer ${GROVE_MCP_TOKEN}" } } } }
```

## Hosting it as a service

The packaged user unit supervises the server and reads its token from `mcp.env`.

```bash
install -m 600 /dev/null ~/.config/grove/mcp.env
echo "GROVE_MCP_TOKEN=$(openssl rand -hex 32)" >> ~/.config/grove/mcp.env
WITH_MCP=1 make systemd && WITH_MCP=1 make systemd-enable
```

## Configuration

Flags override environment variables, which override defaults.

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_API_URL` | `http://127.0.0.1:7421` | The daemon. |
| `GROVE_API_TOKEN` | unset | Bearer for the daemon. Needed only when the URL points elsewhere. |
| `GROVE_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. |
| `GROVE_MCP_HOST`, `GROVE_MCP_PORT`, `GROVE_MCP_PATH` | `127.0.0.1`, `7431`, `/mcp` | Where an HTTP server binds. |
| `GROVE_MCP_TOKEN` | unset | Inbound bearer. Mandatory under a network transport. |
| `GROVE_MCP_READ_ONLY` | off | Register only the ✓ tools. |
| `GROVE_MCP_ALLOWED_HOSTS`, `GROVE_MCP_ALLOWED_ORIGINS` | unset | DNS rebinding allowlists. |

Each setting also has a matching flag. Client registration lives in `.mcp.json` or the client's user config, a hosted server's tokens in `~/.config/grove/mcp.env` at mode `600`, and everything the daemon does in Grove's own config.

## Using Grove from Mewbo

Point Mewbo's MCP pool at either transport and define it as a project agent. See [Agents](configure-agents.md) and [Workspace Lifecycle](features-workspace-lifecycle.md).

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
