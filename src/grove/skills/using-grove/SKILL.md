---
name: using-grove
description: Use when orchestrating a fleet of independent coding agents with Grove from OUTSIDE — isolated per-task workspaces (tmux plus git worktree, host or container) across model providers and harnesses. Triggers on: spinning up, steering, pausing, resuming or killing workspaces, `grove create`/`fleet`/`show`/`message`/`tickets`, the grove_* MCP tools, splitting large or multi-part work across parallel agents, reviewing one diff from several angles, checking what the fleet is doing or who needs attention, linking issues or pull requests to a workspace, and handing a ticket to the fleet by assigning it. Covers the three status axes, the task phase an agent reports for itself, ticket and PR linking and the live status comment it turns on, and how to get agents to keep the tracker current. For configuring Grove itself see configuring-grove; for reporting from inside a workspace see working-in-grove.
---

# Using Grove

[Grove](https://github.com/bearlike/Grove) runs a fleet of coding agents that
live outside your context rather than sub-agents inside it. Each workspace is a
whole separate agent process with its own runtime, its own context window and
its own git worktree, addressable from outside. You create, steer, watch, pause,
resume, respawn and kill them.

Reach for Grove when you want parallel workspaces that outlive a single turn.
For quick in-session parallelism across a few scoped sub-tasks, plain sub-agents
are lighter and fit better.

Grove earns its keep on work too big, too parallel or too cross cutting for one
agent in one context.

- **Decompose and parallelize.** Split by component and spin up one workspace
  per slice. Each gets a clean worktree, so they never touch each other's files.
- **Triage at scale.** Forty Sentry issues or a whole backlog, one workspace per
  issue or per cluster. Each agent investigates independently and you review
  results rather than noise.
- **Work one ticket from several angles at once.** Implementation, tests and
  docs run in parallel in separate environments instead of one after another.
- **Independent review.** Point separate agents, on different models and
  different harnesses, at the same diff for uncorrelated second opinions.

## The model

- **One workspace is one isolated runtime for one agent.** A dedicated git
  worktree, or the repo root for a lightweight case, plus a tmux session running
  that agent's own CLI, on the host or inside a container.
- **Model and harness agnostic by design.** Every workspace picks its own agent
  and model. The point is giving each slice the harness that suits it, not
  stretching one agent across everything.
- **You own the fleet and each member owns its own task.** Decide the split,
  spawn one workspace per slice, then poll and steer and collect. A map step and
  a reduce step, with real isolation so one agent's long tool call never blocks
  another.

## What Grove reports back

Three independent axes describe a workspace and you need all three. Conflating
them is the mistake the design exists to prevent.

| Axis | Answers | Reported by |
|---|---|---|
| Workspace status | Is the runtime there. `active`, `idle`, `paused`, `offline`, `orphaned`, `provisioning`, `error` | Grove observes it |
| Agent activity | Is the agent moving right now. `working`, `waiting`, `blocked`, `error`, `idle` | Grove infers it from transcript, hooks and pane |
| Task phase | How far through the task the agent believes it is | The agent declares it, and nothing derives it |

Phase is the axis you could not get before. An agent reads `working` both while
it is still reading the ticket and while it is pushing the branch, and those two
call for opposite responses from you. Only the agent can tell them apart, which
is why this axis is pushed rather than observed. The six phases run `scoping`,
`planning`, `implementing`, `verifying`, `delivering`, `done`.

Four things about phase are easy to get wrong.

- **A missing phase is not `scoping`.** A workspace that never reported carries
  no phase at all. That is a fact about the agent, where `scoping` is a fact
  about the task, so render them differently.
- **Backwards is a correct report.** An agent that discovers in `verifying` that
  its design was wrong should say `planning` again. Grove never enforces forward
  motion, and an honest reversal is worth more than a phase that only climbs.
- **There is no `blocked` and no `error` phase.** Both already live on the
  activity axis, and an agent stuck on a question is still inside some phase.
- **Grove renders no staleness verdict.** Three hours in `implementing` is a
  long task, not a stale report. Whether the agent is alive is what the other
  two axes answer. The timestamp is the file's own mtime, so hold your own
  policy if you want one.

Phase does not survive `pause`. It lives in the worktree that pause removes, so
a resumed workspace reports afresh and the durable record stays the commit log.

## Watching the fleet

`grove fleet` is the read built for supervising many workspaces at once. It is
host wide rather than repo scoped, takes no arguments and emits JSON. One call
carries every workspace with its state, its reported phase, todo counts, git
ahead and behind, recent commits, and per session activity including which
agents are waiting on you.

```bash
grove fleet | jq '.projects[].workspaces[] | select(.needs_attention)'
```

Prefer it over per workspace phase and peek calls the moment you supervise more
than one agent. `grove_get_fleet_status` is the same payload over MCP. It has no
pagination, so the response grows with the fleet.

For a single workspace, `grove show` gives git, agent, transcript and live pane
state at a glance.

## Tying a workspace to the work item

A workspace carries references to the issues and pull requests it is working.
This is what makes a fleet addressable by work item rather than by workspace id.

```bash
grove tickets attach 42
grove tickets attach https://example.com/acme/widgets/pull/17
grove tickets list
grove tickets detach 42
```

You never name a provider. Grove infers both the provider and whether the ref is
an issue or a pull request from the shape of what you pass. Four input forms
work, a full URL, `#42`, `42`, and `owner/repo#42`. Linear keys such as
`ENG-123` parse for free. Pass `--workspace` to name one, or let Grove infer it
from the directory you are standing in. Attach and detach are both idempotent.

Five things worth knowing before you document or automate this.

- **A pull request is not a separate kind of attachment.** It is one ref list
  with a kind discriminator, so there is no separate verb to go looking for.
  Re-attaching a ref Grove recorded as an issue corrects the kind in place
  rather than duplicating the row.
- **Grove never detects your pull request from the branch.** Branch names yield
  issue refs only. A pull request enters the list solely by explicit attach.
- **Attach stores a link and fetches nothing.** Title, status and assignee are
  fetched on demand and never persisted, which is what keeps attach fast and
  safe offline.
- **A merged pull request reads `merged` and never `closed`.** The issues
  endpoint calls a merged PR closed, which is true and useless, so Grove reads
  the pulls namespace and normalizes. This is how you learn work landed.
- **Attaching a ticket is a private local link.** It does not make an issue
  reference safe to put in pull request text. That ban exists because PR text
  mirrors to public remotes where a bare number resolves against a different
  tracker, and attaching changes nothing about the mirror.
- **Attaching is also what makes the status mirror appear.** When issue-ops is
  enabled, Grove keeps ONE live comment per attached ticket carrying the
  workspace's state, its reported phase and its todo checklist. Nothing is
  published for a workspace with no refs, so an attach is the difference between
  a fleet a human reads from the tracker and one they can only see in Grove.
- **Every one of those comments is authored as whatever account the configured
  token belongs to.** Point it at a bot. Forges set authorship at creation and
  never change it on edit, so a comment first written under a personal token
  stays attributed to that person permanently.
- **Attach succeeding tells you nothing about whether the tracker is reachable.**
  It performs no network call, and publish failures are swallowed so an outage
  cannot break the activity poll — so a misconfigured provider presents as
  silence rather than an error. If comments never appear, suspect the credential
  before the code.

### Keeping the tracker current

The mirror is only as good as what the agent reports, and the agent is the only
one who can report a phase. Two consequences for you as the orchestrator:

- **Tell a workspace to report, and to keep its todo list honest**, in the prompt
  that starts it. An agent that never writes a phase publishes a comment with no
  progress in it, and Grove will not invent one — a missing phase renders as
  nothing rather than as `scoping`. This is the single most common reason a
  status comment looks empty.
- **Attach every ticket the workspace owns, up front.** One report publishes to
  all of them, but only to the ones attached, so a ticket linked late carries no
  history on its own thread.

You can correct a report from outside with `grove phase <ref> <phase>` or
`grove_set_workspace_phase` — for when an agent has stopped reporting or has
plainly mis-stated where it is. Use it to fix the record, not to drive it: a
phase you set is your claim about someone else's work, and the next thing the
agent writes overwrites it.

Before you create a second workspace for a ticket, ask whether one already
exists. Over MCP, `grove_list_workspaces` narrows by `ticket_provider` plus
`ticket_id` and answers exactly that, which is the difference between steering
the workspace already on the job and starting a duplicate.

### Handing work over by assignment

The tracker's assignee field can be the fleet's inbound queue. With issue-ops
pickup enabled, the daemon polls each configured tracker for open issues
assigned to Grove's account and starts a workspace on each one, under a ceiling
on how many pickup-started workspaces may be live at once. `grove tickets
handover <ref>` does the same thing on the spot rather than waiting for the
poll, `grove tickets owned` lists what the fleet is holding host wide, and
`grove tickets handback <ref>` unassigns without touching the workspace.

Assigning on the tracker and handing over from the CLI take the same path, so a
ticket handed over is never picked up twice. Handover needs a provider with an
owner and a repo configured, which is Gitea and GitHub; Linear refuses, because
two repos' issue #7 would otherwise share one marker.

Gitea, GitHub and Linear are supported and all three stay off until a repo opts
in. Each wants `enabled`, its scope, a base URL, and the NAME of an env var
holding the token rather than a token literal. With nothing configured, an
attach by bare ref fails loudly and names the providers that are enabled.

## CLI

Run from inside any git repo Grove knows about. `grove config add-project`
registers a repo you are not currently standing in.

| Command | What it does |
|---|---|
| `grove create <title> [--agent <name>] [--model <id>] [--prompt <text>] [--runtime host\|container] [--branch/--base/...]` | Spin up a workspace and kick it off with a first task |
| `grove ls` | This repo's workspaces, as JSON |
| `grove show [<ref>]` | One workspace's git, agent, transcript and live pane state |
| `grove fleet` | Every workspace on the host, as JSON |
| `grove message <ref> <text>` | Steer a running agent |
| `grove phase [<ref>] [<phase>] [--note <text>]` | Read a phase, or set one from outside |
| `grove tickets attach\|list\|detach <ref> [--workspace <id>]` | Issue and pull request links |
| `grove tickets handover <ref>`, `grove tickets owned`, `grove tickets handback <ref>` | Hand an issue to the fleet by assigning it, list what the fleet holds, give one back |
| `grove pause\|resume\|respawn\|kill <ref>` | Lifecycle |
| `grove attach <ref>` | Drop into the workspace's live tmux session yourself |
| `grove shell <ref>` | An interactive shell inside a container workspace, persistent across visits |
| `grove code <ref>` | Attach VS Code to a container workspace |
| `grove agent list\|add\|kill\|peek\|message\|attach` | Several agents sharing one container workspace |
| `grove sessions list\|show\|dump\|remap` | Recorded agent transcripts across the project |
| `grove doctor` | Check the host dependencies each runtime needs |
| `grove config`, `grove init devcontainer`, `grove skills install`, `grove mcp install` | Setup and onboarding |

`<ref>` is a workspace id or a unique id prefix. `grove <command> --help` carries
every flag. `grove shell`, `grove code` and the whole `grove agent` group require
a containerized workspace and error cleanly on a host one.

A fresh container workspace reads `provisioning` while its image builds, which
can be minutes. That is the one status whose remedy is to wait, and it ends on
its own — `respawn` is the one action that destroys the build in flight.

`--runtime` is fixed at create time and never editable afterwards. If a container
was wanted and the runtime was unavailable, the workspace falls back to host and
names the reason, which `grove show` and `grove ls` report. Fix the runtime, then
`grove respawn` to promote it. A container workspace with no project
`.devcontainer/` runs Grove's default image, and `grove init devcontainer`
graduates it to a committed config.

## MCP

The same fleet, over MCP. What your server actually registers is the census;
each tool's own schema documents its inputs, so read those rather than guessing
from here.

| Group | Tools |
|---|---|
| Discovery | `grove_list_projects`, `grove_list_workspaces`, `grove_get_workspace`, `grove_list_agents`, `grove_list_sessions` |
| Watching | `grove_get_fleet_status`, `grove_peek_workspace`, `grove_get_workspace_phase`, `grove_get_workspace_todo`, `grove_attach_instruction` |
| Lifecycle | `grove_create_workspace`, `grove_pause_workspace`, `grove_resume_workspace`, `grove_respawn_workspace`, `grove_kill_workspace` |
| Steering | `grove_send_workspace_message`, `grove_set_workspace_phase` |
| Links and sessions | `grove_attach_ticket`, `grove_detach_ticket`, `grove_remap_workspace_session` |

`grove_list_projects` takes no arguments and returns the repo roots every other
tool wants, so start there.

- `grove_kill_workspace` requires `delete_branch` explicitly and rejects the call
  without it. Destructive tools never guess.
- `grove_send_workspace_message` that times out means delivery is unknown rather
  than failed. Peek before you resend, or the agent gets the message twice.
- `grove_set_workspace_phase` is for correcting an agent's report from outside.
  The agent's own channel is a file, described below.
- A server started with `--read-only` withholds every mutating tool by not
  registering it, because a tool an agent can see is a tool it will try to call.
- `grove_peek_workspace` caps its pane snapshot, and a trailing ellipsis is the
  signal that it trimmed.

## What a container workspace cannot do

A containerized agent cannot reach Grove at all. The daemon binds loopback with
no route from inside, the `grove` command is not installed there, and Grove
deliberately leaves its MCP server out of the seeded config rather than register
a binary that is absent. So that agent has no CLI verbs and no Grove tools.

It reports its phase by writing a file, which the worktree mount carries to the
host the instant it lands. Everything else you want done to that workspace, you
do from outside, including attaching the pull request it opened.

You do not have to restate the reporting contract in your prompt. By default
Grove hands every new workspace a one-paragraph brief on its first turn
pointing it at the companion `working-in-grove` skill, and `--no-brief` turns
that off per workspace. How it arrives differs, and the difference bites in a
container: a host `claude_code` agent gets it from Grove's status hook whether
or not you gave it a task, while an agent with no such hook gets it prepended to
the initial prompt — so a container workspace created with no `--prompt` is
never briefed at all.

## The other Grove skills

- **`working-in-grove`** — the same fleet seen from the inside. Point a workspace
  agent at it rather than restating the phase contract in every prompt.
- **`configuring-grove`** — the config cascade, agents, init scripts, containers,
  and ticket providers. Reach for it when a workspace will not start the way you
  meant, or when the status mirror is silent.
- **`reinstalling-grove`** — when an update did not take: stale daemon, stale
  webapp build, old UI after a pull.

## Configuration

Setting Grove up is a separate concern from using the fleet. If a
`configuring-grove` skill is available here, use it for that. `grove skills
install` and `grove mcp install`, or `grove config init --with-onboarding`, is
how this skill and Grove's MCP server arrived in the first place. For anything
this skill does not cover, `grove --help` or a repo aware research tool pointed
at `bearlike/Grove` will get you unstuck faster than guessing.
