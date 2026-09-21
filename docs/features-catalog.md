# Capability catalog

## Find what Grove can do

Every capability on one page, one row each, linked to the page that explains
it. Read down a table to see what an area covers. Follow a link for the depth.

## Surfaces

| Capability | What it does | Page |
|---|---|---|
| Terminal UI | Create, attach, pause and kill in one keypress beside a live peek rail. | [TUI tour](use-tui.md) |
| CLI | Every verb as a command, with tab completion and an agent friendly contract. | [CLI](use-cli.md) |
| Web dashboard | The fleet, one workspace's transcript and work panel, sessions and usage from any device. | [Web dashboard](use-webapp.md) |
| MCP server | Claude Code, Codex and OpenCode drive workspaces themselves over stdio or HTTP. | [MCP server](use-mcp.md) |
| Issue ops | A tracker comment or assignment starts and steers a workspace. | [Issue ops](issue-ops.md) |
| Device pairing | A new browser pairs once with a code the host approves. | [Authentication](use-auth.md) |

## Workspaces

| Capability | What it does | Page |
|---|---|---|
| One agent, one worktree | Each workspace is its own git worktree, branch and tmux session. | [Workspace lifecycle](features-workspace-lifecycle.md) |
| Pause, resume, respawn, kill | Pause drops the worktree and keeps the branch. Kill deletes only branches Grove created. | [Workspace lifecycle](features-workspace-lifecycle.md) |
| Branch provenance | Grove records whether it made a branch or you attached one, and kill honours it. | [Branch provenance](features-branch-provenance.md) |
| Containers | A workspace runs inside the project's devcontainer with its own egress policy. | [Containerized agents](features-containers.md) |
| Init scripts | A script prepares each new worktree, on the host or in the container. | [Init scripts](configure-init-scripts.md) |
| Root placement | A workspace can run in the repo root on the branch already checked out. | [Workspace lifecycle](features-workspace-lifecycle.md#root-workspaces) |
| Status semantics | Three axes, workspace, agent activity and task phase, each answering a different question. | [Status semantics](features-status.md) |

## Working with an agent

| Capability | What it does | Page |
|---|---|---|
| Steering | Send a message to a running agent without attaching. | [Web dashboard](use-webapp.md#steering-the-agent) |
| Questions and plans | A live question renders as a card and a plan approval as a dialog row. | [Web dashboard](use-webapp.md#steering-the-agent) |
| Attachments | Paste or attach an image or file and the agent is handed its path. | [Attachments](features-attachments.md) |
| Image annotation | Draw over a staged image before sending it. | [Attachments](features-attachments.md#annotate-before-you-send) |
| Diagram collaboration | A person and an agent edit one draw.io file with revision checks. | [Diagram collaboration](features-diagrams.md) |
| Mockups and specs | Approve a UI mockup or an architecture plan as a picture before code exists. | [Diagram collaboration](features-diagrams.md#two-jobs-for-one-file) |
| Model per workspace | Each agent exposes its own catalog and a workspace picks one. | [Web dashboard](use-webapp.md#starting-a-workspace) |
| First turn brief | Grove tells the agent where it is and how to report. | [Project setup](configure-project.md#tailor-the-first-turn-brief) |
| Native sessions | Grove owns each Claude Code, Codex and OpenCode session by default, routes its controls through the provider protocol, lets each workspace override the mode, and names the event log tab Stream. | [Agents](configure-agents.md#native-sessions-and-terminal-twins) |
| Claude Code adapter | Confirmed message delivery, validated model changes and structured questions over JSON streaming. | [Claude Code](agents-claude-code.md) |
| Codex adapter | Turn steering with expected turn checks, plus command and file change approvals over JSON RPC. | [Codex](agents-codex.md) |
| OpenCode adapter | A Grove owned HTTP server, per session event filtering and a read only shared transcript database. | [OpenCode](agents-opencode.md) |
| Compaction record | Each context cut reports the tokens dropped, its duration and the model that compacted. | [Agent activity](features-activity.md#where-the-context-was-compacted) |
| Peek rail | Read the selected pane live without attaching. | [Peek rail](features-activity.md#the-peek-rail) |

## Trackers

| Capability | What it does | Page |
|---|---|---|
| Ticket providers | Attach a GitHub, Gitea or Linear issue or pull request to a workspace. | [Ticket providers](features-ticket-providers.md) |
| Refs from the branch | Grove reads the ticket off the branch name so the link follows the branch. | [Ticket providers](features-ticket-providers.md#deriving-refs-from-the-branch-name) |
| Sticky comment | One comment on the ticket carries phase, checklist, branch, commit and links. | [Issue ops](issue-ops.md) |
| Task phase per ticket | Each attached ticket reports its own six stage phase and a blocked flag. | [Status semantics](features-status.md#the-third-axis-task-phase) |

## Observability

| Capability | What it does | Page |
|---|---|---|
| Activity dashboard | Which agent is working, waiting, blocked or idle, across every repo. | [Agent activity](features-activity.md) |
| Session catalog | Every agent session on the host, including work Grove did not launch. | [Agent activity](features-activity.md#the-session-catalog-every-session-on-this-host) |
| Session recovery | Sessions re-adopt across daemon restarts and a stale pointer remaps in a click. | [Workspace lifecycle](features-workspace-lifecycle.md#recovery-from-a-vanished-session) |
| Telemetry | One session becomes one trace, sent to Langfuse or any OTLP backend. | [Telemetry](features-telemetry.md) |
| Usage audit | Tokens, time, cost and subscription windows projected from transcripts on disk. | [Web dashboard](use-webapp.md#the-usage-audit) |
| Push notifications | A push when an agent finishes a turn or needs an answer. | [Push notifications](features-notifications.md) |
| Public share links | A read only transcript link with an expiry and an optional passcode. | [Web dashboard](use-webapp.md#working-in-a-session) |

## Configuration

| Capability | What it does | Page |
|---|---|---|
| Cascade | Seven layers from built in to invocation, mechanism never policy. | [Configuration cascade](features-cascade.md) |
| Project config | One committed file carries agents, setup and container policy for the team. | [Project setup](configure-project.md) |
| Agent roster | Add, override or hide agents and their model catalogs. | [Agents](configure-agents.md) |
| Ticket providers | Tokens by environment variable name, never a literal. | [Ticket providers](configure-ticket-providers.md) |
| Reference | Every field, generated from the schema. | [Configuration reference](configure-reference.md) |
