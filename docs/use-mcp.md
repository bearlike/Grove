# MCP Server

Grove ships an MCP server: `grove-mcp`. It exposes your workspace fleet as tools that any MCP-capable agent can call. Think of the Grove daemon as a control tower and `grove-mcp` as its radio: an assistant like Mewbo or Claude tunes in and can list, create, steer, and tear down workspaces without touching git or tmux itself.

The server is a thin adapter. Every tool call travels through the same daemon REST API the TUI and web dashboard use, so all of Grove's safety rules apply unchanged: your git stays yours.

## Setup

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

## Configuration

`grove-mcp` speaks stdio and takes its two knobs from the environment (a CLI flag wins over the environment):

| Setting | Default | Purpose |
|---|---|---|
| `GROVE_API_URL` (or `--api-url`) | `http://127.0.0.1:7421` | Base URL of the running Grove daemon. |
| `GROVE_API_TOKEN` | unset | Bearer token for the daemon. Leave unset on the daemon's own host: the server mints a local session automatically, the same way the TUI does. Set it when the URL points at another machine (pair once, then export the token). |

## Tools

| Tool | What it does |
|---|---|
| `grove_list_workspaces` | List every workspace with id, branch, agent, and status. |
| `grove_get_workspace` | Full state for one workspace by id. |
| `grove_create_workspace` | Create a workspace: worktree, branch, tmux session, agent. Params: `repo_root`, `title`, `agent_name`, optional `description`, `branch_plan` (`auto` default, `new_named`, `existing_local`, `track_remote`, `root`), `skip_init`, `initial_prompt`. Pass `initial_prompt` to deliver the agent's first task race-free at boot so the workspace starts working immediately; omit it to boot idle and steer later with `grove_send_workspace_message`. |
| `grove_peek_workspace` | Bounded snapshot: ahead/behind, diff stats, dirty files, recent commits, capped pane output. |
| `grove_pause_workspace` | Remove worktree and session, keep the branch. Refuses dirty worktrees unless `force` is true. |
| `grove_resume_workspace` | Recreate a paused workspace from its branch. |
| `grove_respawn_workspace` | Recreate a vanished tmux session for an offline workspace. |
| `grove_kill_workspace` | Destroy a workspace. `delete_branch` is required and has no default: the caller must state intent. Remote branches are never touched. |
| `grove_attach_instruction` | The `tmux attach` command for handing a session to a human. |
| `grove_send_workspace_message` | Send a steering message to the workspace's agent. Returns `status="sent"` on success or `status="unavailable"` when the connected daemon predates the messaging endpoint. |

Every response is structured JSON with explicit status fields and stable workspace ids, so a calling agent never has to parse prose to learn what happened.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools: create, list, peek, pause, and steer workspaces" /></div>
  <figcaption class="ms-shot__body">The Grove MCP tools as seen from Claude Code. Create, list, peek, pause, resume, kill, and steer workspaces through a single connected server.</figcaption>
</figure>

## Using Grove from Mewbo

Mewbo can adopt Grove as its durable workspace backend over this server: Mewbo's hypervisor calls the tools above, Grove remains the system of record for worktrees, branches, sessions, and lifecycle state. The division of responsibility is deliberate. Grove owns the workspace control plane; Mewbo owns scheduling, skills, and conversation. Point Mewbo's MCP pool at the `mcp.json` entry shown above and it discovers the tool surface automatically.

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
