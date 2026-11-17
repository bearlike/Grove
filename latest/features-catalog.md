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
| Slack | Mention Grove in a thread to start a workspace. Replies steer it and the agent's answers come back. | [Slack](features-slack.md) |
| Device pairing | A new browser pairs once with a code the host approves. | [Authentication](use-auth.md) |
| Sign in with OIDC | People sign in through your identity provider, in the browser or the CLI, and the fleet opens on their own workspaces. | [Authentication](use-auth.md#sign-in-as-a-person) |

## Workspaces

| Capability | What it does | Page |
|---|---|---|
| One agent, one worktree | Each workspace is its own git worktree, branch and tmux session. | [Workspace lifecycle](features-workspace-lifecycle.md) |
| Pause, resume, respawn, kill | Pause drops the worktree and keeps the branch. Kill deletes only branches Grove created. | [Workspace lifecycle](features-workspace-lifecycle.md) |
| Branch provenance | Grove records whether it made a branch or you attached one, and kill honours it. | [Branch provenance](features-branch-provenance.md) |
| Containers | Run each agent in a containerized sandbox. Resource limits protect the rest of the fleet. | [Containerized agents](features-containers.md) |
| Remote runtimes | Serverless platforms as a third runtime beside host and container, resident or one turn at a time. | [Runtimes](features-runtimes.md) |
| Federation | Several daemons behind one hub that places creates by label and capacity and forwards every verb to the owner. | [Federation](features-federation.md) |
| Reaping | Leaked tmux servers, processes and files are marked, given a grace and swept; `grove vacuum` runs it by hand. | [Reaping](features-reaping.md) |
| Idle suspension | An agent at rest past a cooldown has its container paused in place; the next message wakes it. | [Idle suspension](features-idle.md) |
| Init scripts | A script prepares each new worktree, on the host or in the container. | [Init scripts](configure-init-scripts.md) |
| Root placement | A workspace can run in the repo root on the branch already checked out. | [Workspace lifecycle](features-workspace-lifecycle.md#root-workspaces) |
| Status semantics | Three axes, workspace, agent activity and task phase, each answering a different question. | [Status semantics](features-status.md) |

## Working with an agent

| Capability | What it does | Page |
|---|---|---|
| Steering | Send a message to a running agent without attaching. | [Web dashboard](use-webapp.md#steering-the-agent) |
| Questions and plans | A live question renders as a card and a plan approval as a dialog row. | [Web dashboard](use-webapp.md#steering-the-agent) |
| Attachments | Paste or attach an image or file and the agent is handed its path. | [Attachments](features-attachments.md) |
| Artifacts | A file or a link the agent attaches, opened read-only from the workspace. | [Artifacts](features-artifacts.md) |
| Image annotation | Draw over a staged image before sending it. | [Attachments](features-attachments.md#annotate-before-you-send) |
| Dictation | A mic on the web composer records, transcribes and inserts the text into your draft. Nothing is sent until you send. | [Web dashboard](use-webapp.md#dictation-and-read-aloud) |
| Read aloud | A speaker button under each answer plays it, and a second click stops it. | [Web dashboard](use-webapp.md#dictation-and-read-aloud) |
| Diagram collaboration | A person and an agent edit one [draw.io](https://www.drawio.com/) file with revision checks. | [Diagram collaboration](features-diagrams.md) |
| Mockups and specs | Approve a UI mockup or an architecture plan as a picture before code exists. | [Diagram collaboration](features-diagrams.md#two-jobs-for-one-file) |
| Model per workspace | Each agent exposes its own catalog and a workspace picks one. | [Web dashboard](use-webapp.md#starting-a-workspace) |
| First turn brief | Grove tells the agent where it is and how to report. | [Project setup](configure-project.md#tailor-the-first-turn-brief) |
| Native sessions | Grove owns each Claude Code, Codex and OpenCode session by default, routes its controls through the provider protocol, lets each workspace override the mode, and names the event log tab Stream. | [Agents](configure-agents.md#native-sessions-and-terminal-twins) |
| Claude Code adapter | Confirmed message delivery, validated model changes and structured questions over JSON streaming. | [Claude Code](agents-claude-code.md) |
| Codex adapter | Turn steering with expected turn checks, plus command and file change approvals over JSON RPC. | [Codex](agents-codex.md) |
| OpenCode adapter | A Grove owned HTTP server, per session event filtering and a read only shared transcript database. | [OpenCode](agents-opencode.md) |
| Compaction record | Each context cut reports the tokens dropped, its duration and the model that compacted. | [Agent activity](features-activity.md#where-the-context-was-compacted) |
| Grove commands | `/grove:<name>` runs a configured chain of session controls in order, such as compacting on a fast model and switching back. | [Choosing an agent](configure-agents.md#grove-commands) |
| Peek rail | Read the selected pane live without attaching. | [Peek rail](features-activity.md#the-peek-rail) |

## Trackers

| Capability | What it does | Page |
|---|---|---|
| Ticket providers | Attach a Linear, GitHub or Gitea issue or pull request to a workspace. | [Ticket providers](features-ticket-providers.md) |
| Refs from the branch | Grove reads the ticket off the branch name so the link follows the branch. | [Ticket providers](features-ticket-providers.md#deriving-refs-from-the-branch-name) |
| Sticky comment | One comment on the ticket carries phase, checklist, branch, commit and links. | [Issue ops](issue-ops.md) |
| Task phase per ticket | Each attached ticket reports its own six stage phase and a blocked flag. | [Status semantics](features-status.md#the-third-axis-task-phase) |
| Ticket change mail | A person's change to an attached issue or PR is mailed to its workspace, once a minute at most. | [Ticket providers](features-ticket-providers.md#changes-reach-the-agent) |
| Watches | Wait on CI, a timer or a command without polling; the outcome or the expiry arrives as mail. | [Watches](features-watches.md) |

## Observability

| Capability | What it does | Page |
|---|---|---|
| Activity dashboard | Which agent is working, waiting, blocked or idle, across every repo. | [Agent activity](features-activity.md) |
| Session catalog | Every agent session on the host, including work Grove did not launch. | [Agent activity](features-activity.md#the-session-catalog-every-session-on-this-host) |
| Session recovery | Sessions re-adopt across daemon restarts and a stale pointer remaps in a click. | [Workspace lifecycle](features-workspace-lifecycle.md#recovery-from-a-vanished-session) |
| Telemetry | Send each turn to an OTLP backend as one trace, with workspace, branch and ticket on every span. | [Telemetry](features-telemetry.md) |
| Feedback and evaluations | Rate turns in Grove, and label every observation with a local decision model calibrated on your own traffic. | [Feedback and evaluations](features-feedback-evals.md) |
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
