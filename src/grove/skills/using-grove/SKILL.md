---
name: using-grove
description: Use when you want to orchestrate a fleet of independent, parent-level coding agents with Grove — spin up isolated per-task workspaces (tmux + git worktree, host or container) across model providers and harnesses, to divide, parallelize, and independently review large or multi-part work. Covers the CLI verbs and the MCP tool surface by id.
---

# Using Grove

[Grove](https://github.com/bearlike/Grove) manages a **fleet of independent,
parent-level coding agents** — not sub-agents inside your own context. Where
you'd spawn sub-agents that share your session, Grove spins up whole separate
agent processes, each with its own runtime, its own context window, and its
own git worktree (or the repo root) — addressable from the outside: create,
message, watch, pause, resume, respawn, kill.

Reach for it when a task is too big, too parallel, or too cross-cutting for
one agent in one context:

- **Decompose and parallelize.** "Build the backend while another agent
  builds the frontend" — split by component, spin up one workspace per slice,
  each in its own clean worktree so they never step on each other's files.
- **Triage at scale.** Forty Sentry issues, a Linear backlog — one workspace
  per issue or per cluster, each agent investigates and reports back
  independently; you review the results, not the noise.
- **Work one ticket from several angles at once.** Different aspects of the
  same ticket (implementation, tests, docs) each in their own clean
  environment, run in parallel instead of serially.
- **Independent review.** Point separate agents (different models, different
  harnesses) at the same diff for uncorrelated second opinions.

## The model

- **One workspace = one isolated runtime for one agent.** A dedicated git
  worktree (or the repo root for a lightweight/ad-hoc case) plus a tmux
  session running that agent's own CLI — Claude Code, Codex, or any other
  configured harness — on the host or inside a container.
- **Model- and harness-agnostic by design.** Every workspace picks its own
  agent and model. The point is giving each slice of work the harness best
  suited to it, not stretching one agent across everything.
- **You own the fleet; each member owns its own task.** Decide how to split
  work (by component, by ticket, by review lens, by repo), spawn one
  workspace per slice, then poll/steer/collect — a map step and a reduce
  step, with real isolation so one agent's mistakes or long tool calls never
  block another's.

## CLI — the fleet control surface

Run from inside any git repo Grove knows about (`grove config add-project`
registers a repo you're not currently inside):

- `grove create <title> [--agent <name>] [--model <id>] [--message <prompt>] [--branch/--base/...]`
  — spin up a new workspace and kick it off with an initial task.
- `grove ls` — list this repo's workspaces (JSON).
- `grove show [<ref>]` — one workspace's git + agent + transcript + live-pane
  state at a glance.
- `grove message <ref> <text>` — steer a running workspace.
- `grove pause|resume|respawn|kill <ref>` — lifecycle.
- `grove attach <ref>` — drop into the workspace's live tmux session yourself.
- `grove sessions list|show|dump` — browse recorded agent-session transcripts
  across the project.

`<ref>` is a workspace id or unique id prefix. Full flag reference for any
command: `grove <command> --help`.

## MCP — the same fleet, as tools

If Grove's MCP server is connected, the same lifecycle is available as tools
— useful for managing the fleet without shelling out: `grove_create_workspace`,
`grove_list_workspaces`, `grove_get_workspace`, `grove_peek_workspace`,
`grove_send_workspace_message`, `grove_pause_workspace`, `grove_resume_workspace`,
`grove_respawn_workspace`, `grove_kill_workspace`, `grove_attach_instruction`,
`grove_remap_workspace_session`, `grove_list_agents`. Each tool's own schema
documents its inputs — read those rather than guessing here.

## Configuration

Setting up agents, init scripts, models, or MCP servers *for* Grove itself is
a separate concern from *using* the fleet. If a `configuring-grove` skill is
available in this environment, use it for that; `grove skills install` /
`grove mcp install` (or `grove config init --with-onboarding`) is how this
skill and Grove's own MCP server got here in the first place. For anything
this skill doesn't cover, `grove --help` or a repo-aware research tool
pointed at `bearlike/Grove` will get you unstuck faster than guessing.
