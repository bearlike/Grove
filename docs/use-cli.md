# CLI

## Manage your fleet from the command line

Every command and flag reaches the same engine as the TUI.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/demos/cli-completion.gif" alt="Pressing TAB through grove commands and model names in a terminal, with each completion list drawn from live values" /></div>
  <figcaption class="ms-shot__body">Completion reads live values. Workspace ids, configured agents, models, branches and sessions appear at <kbd>Tab</kbd>. See <a href="#tab-completion">tab completion</a>.</figcaption>
</figure>

## Find the right workflow

- `grove --help` lists commands and `grove <topic> --help` lists a topic's flags.
- `grove skills list --details` lists every installed workflow and `grove skills show <name>` reads one, with no daemon needed.
- Any agent writes to another with `grove mailbox contacts` then `grove mailbox send --to <id> --subject … --body …`. A reply is the same command with the addresses swapped. See [native sessions](configure-agents.md#native-sessions-and-terminal-twins) and the [MCP learning path](use-mcp.md#agents-writing-to-each-other).

## The contract

- Most commands resolve the repo from `cwd` upward. `daemon`, `auth`, `fleet` and `tickets owned` are host wide.
- A WORKSPACE argument takes an exact id or a unique prefix, and an ambiguous one exits `1` with the candidates.
- `ls`, `fleet` and `debug` are JSON native and the session commands take `--json`.
- Exit `0` succeeds, `1` is a Grove error, `2` is bad input. No pagers, and no prompt but `grove kill`'s, which `--yes` skips.

## `grove`

Launches the TUI at `cwd`. See the [TUI tour](use-tui.md) and [tab completion](#tab-completion).

```bash
cd /path/to/my-project
grove
```

## Read commands

Read workspace state and transcripts without changing them.

### `grove ls`

Lists repository workspaces as JSON. See [status semantics](features-status.md).

```bash
grove ls
# Every workspace that needs attention, without opening the TUI.
grove ls | jq -r '.[] | select(.status=="offline" or .status=="orphaned" or .status=="error") | "\(.status)\t\(.title)\t\(.branch)"'
```

### `grove fleet`

Lists host workspaces as JSON.

```bash
grove fleet
grove fleet | jq '.projects[].workspaces[] | select(.needs_attention)'
```

### `grove show`

One workspace's identity, git counts, agent state, [task phase](features-status.md#the-third-axis-task-phase) and todo list, recent turns and a live pane snapshot. The peek rail as a command.

```bash
grove show [WORKSPACE] [--last/-l N]

grove show            # from inside a worktree, infers the workspace
grove show a1b2 -l 5  # by id prefix, last 5 turns
```

### `grove edit`

Changes a workspace title or description.

```bash
grove edit [WORKSPACE] [--title TEXT] [--description TEXT]

grove edit --title "quota gateway"    # from inside the worktree
grove edit a1b2 -d "spike, do not merge"
grove edit --description ""           # clear the description
```

A title is 1 to 120 characters, an empty description clears it, `--json` returns the record, and the TUI and web dashboard rename through [`grove edit`](#grove-edit) too.

### `grove phase`

Sets or reads [task phase](features-status.md#the-third-axis-task-phase).

```bash
grove phase [REF] [PHASE] [--note TEXT] [--ticket PROVIDER:ID] [--blocked]   # set
grove phase [REF]                                                            # read

grove phase implementing --note "wiring the CLI verb"   # from inside the worktree
grove phase a1b2 verifying                    # another workspace, by id prefix
grove phase verifying --ticket gitea:42       # scoped to one attached ticket
grove phase implementing --blocked            # stuck on this step
grove phase                                   # read the cwd-inferred phase, tickets included
```

- The phases are `scoping`, `planning`, `implementing`, `verifying`, `delivering` and `done`, and a note is one line under 200 characters.
- `--ticket` scopes the claim to one attached ticket's `provider:id` for a workspace working several at once.
- `--blocked` is a flag beside the phase, never a replacement for it.

### `grove sessions`

Lists project conversations, newest first. [`grove sessions recollect`](#grove-sessions-recollect-and-grove-recollect) recovers compacted instructions.

#### `grove sessions list`

```bash
grove sessions list [--host] [--agent KIND] [-w PREFIX] [--since WINDOW] [-n N] [--json]
```

`--host` scans every repo, see [the Session Catalog](features-activity.md#the-session-catalog-every-session-on-this-host). `--since` takes `30m`, `6h`, `2d`, `1w` or an ISO date.

```bash
# Which agents are waiting for a human?
grove sessions list --json \
  | jq -r '.[] | select(.state=="waiting" or .state=="blocked") | "\(.state)\t\(.workspace_title // "-")\t\(.title // .last_prompt)"'

# Only what moved in the last two hours, five most recent.
grove sessions list --since 2h --limit 5
```

#### `grove sessions show`

```bash
grove sessions show REF [-l N] [--json]

grove sessions show 7b3f2c1a --last 3
```

#### `grove sessions dump`

```bash
grove sessions dump REF [--jsonl]

grove sessions dump 7b3f2c1a --jsonl | jq -c 'select(.type=="assistant")'   # original lines verbatim
```

### `grove sessions recollect` and `grove recollect`

Recovers every direct user query from a session, the workspace primary when omitted.

```bash
grove sessions recollect [SESSION] [--last/-l N] [--json]
grove recollect [SESSION] [--last/-l N] [--json]

# Recover the original task after a compaction.
grove recollect 7b3f2c1a

# Machine-readable most recent instructions.
grove sessions recollect 7b3f2c1a --last 3 --json
```

#### `grove sessions remap`

Pins the workspace primary session. See [`cli_sessions.py`](repo:src/grove/tui/cli_sessions.py).

```bash
grove sessions remap WORKSPACE SESSION

grove sessions remap a1b2 cafef00d
```

## Lifecycle commands

Create, reach and remove workspaces.

### `grove create`

Creates a worktree, branch and agent.

```bash
grove create [TITLE] [--agent/-a NAME] [--model/-m ID] [--runtime host|container] [branch flags] [--base REF] [--description/-d TEXT] [--no-init] [--prompt/-p TEXT] [--brief/--no-brief] [--native/--terminal] [--cwd PATH] [--resume-session ID] [--attach/--no-attach]
```

- **Branch.** Auto names one from the title, `--branch` names it off `--base`, `--checkout` reuses a local branch, `--track` follows a remote one, and `--root` runs in the repo root with no worktree.
- **Agent and model.** `--agent` matches a name in your config, `--model` is forwarded verbatim and never validated, and `--native` or `--terminal` overrides the roster entry's session mode for this workspace.
- **Runtime and setup.** `--runtime host|container` is create time only. `--no-init` skips the init script once, `--cwd` starts the agent in a directory inside the worktree, and `--brief` or `--no-brief` decides whether the agent gets Grove's first turn note.
- **The first turn.** `--prompt` delivers the first task at boot, `--resume-session` continues an existing session by id, and `--attach` hands your terminal over once it is up, which is the default when output is a terminal.

```bash
# Quick create: saved defaults, generated title, attached
grove create

# Auto branch (title slug → grove/fix-login-YYYYMMDD-HHmmss)
grove create "fix login" --agent claude

# Named branch off main
grove create "fix login" --agent claude --branch fix/login --base origin/main

# Reuse an existing local branch
grove create "review pr" --agent claude --checkout existing-wip

# Track a remote branch
grove create "track ci" --agent claude --track origin/feature/ci

# In-place (no worktree, no new branch)
grove create "in place" --agent claude --root

# Start working immediately
grove create "add cache" --agent claude --prompt "add an LRU cache in front of the API client"

# Start the agent in a monorepo's API directory
grove create "add endpoint" --agent claude --cwd services/api
```

### Steer, pause, resume, respawn, kill, attach, shell

```bash
grove message a1b2 "now add a test for the empty case"   # a direction to a running agent
grove pause a1b2 --force        # drop worktree and session, keep the branch; --force discards uncommitted changes
grove resume a1b2               # rebuild a paused workspace from its branch
grove respawn a1b2              # recreate a vanished tmux session over the worktree
grove kill a1b2 --keep-branch -y   # remove the workspace; --delete-branch or --keep-branch override provenance, -y skips the prompt
grove attach a1b2               # hand your terminal to the session
grove shell a1b2                # an interactive shell inside the container
```

A native workspace attaches read only. Its pane is Grove's worker printing one protocol frame per line, so steer it with `grove message` or the dashboards.

### `grove agent`

Manages additional container agents.

```bash
grove agent list WORKSPACE
grove agent add WORKSPACE [--agent NAME] [--name SLOT] [--model ID] [--prompt TEXT]
grove agent attach WORKSPACE SLOT
grove agent peek WORKSPACE SLOT [--lines N]
grove agent message WORKSPACE SLOT TEXT
grove agent kill WORKSPACE SLOT [--yes]

grove agent add a1b2 --agent codex --name reviewer
grove agent message a1b2 reviewer "review the last three commits"
```

### `grove code`

Opens a container workspace in VS Code. See [Container Workspaces](features-containers.md#opening-it-in-vs-code).

```bash
grove code WORKSPACE
grove code a1b2
```

### `grove diagram`

Opens, reads, updates or stops an existing `.drawio` file without creating or publishing it.

```bash
grove diagram open PATH
grove diagram read
grove diagram preview
grove diagram update INPUT --revision HASH --session-id ID
grove diagram stop --revision HASH --session-id ID
```

- Use the latest revision and session identity for `update` or `stop`.
- Stopping leaves it readable. See [Diagram collaboration](features-diagrams.md).

### `grove tickets`

Attaches tickets and hands issues to the fleet.

```bash
grove tickets attach REF [--workspace/-w ID]
grove tickets list [--workspace/-w ID]
grove tickets detach REF [--workspace/-w ID]
grove tickets handover REF
grove tickets owned
grove tickets handback REF

grove tickets attach https://github.com/acme/api/pull/42   # cwd-inferred workspace
grove tickets attach '#42' -w a1b2
grove tickets list
```

```bash
grove tickets handover 42   # assign Grove's account, start a workspace on it
grove tickets owned         # every ticket Grove holds, host-wide, and whether work is live
grove tickets handback 42   # unassign, leaving any workspace alone
```

- `--workspace` defaults to the current worktree.
- See [ticket providers](features-ticket-providers.md).

## Using the CLI from an agent

A coding agent with shell access inside a Grove managed worktree has the full command surface, reading what siblings are doing and, when authorized, driving the same fleet.

- Pass `--json` whenever you parse the result. Treat a non zero exit as no data.
- Stop at the first rung that answers your question. `list` is cheapest, `show` and `recollect` cost a few turns, `dump` is the last resort and goes to a file, never your context.

```bash
# Sibling sessions: everything on this host except your own branch.
MY_BRANCH=$(git branch --show-current)
grove sessions list --json \
  | jq -r --arg me "$MY_BRANCH" '
      .[] | select(.git_branch != $me)
      | "\(.session_id[0:8])  \(.state)  \(.workspace_title // "-")  \(.title // .last_prompt // "")"'

grove sessions list --since 1h                 # cheapest: what moved recently
grove sessions show a91e0d34 --last 5          # mid cost: a sibling's last turns
grove recollect a91e0d34 --last 5              # every direct instruction, including pre-compaction
grove sessions dump a91e0d34 --jsonl > /tmp/a91e0d34.jsonl   # last resort

# Spin one up, steer it, check it, pause it.
grove create "add retry logic" --agent claude --prompt "wrap the API client's fetch with exponential backoff"
grove message a1b2 "now add a test for the retry case"
grove sessions show a1b2 --last 3
grove pause a1b2
```

## Admin commands

One fence per verb. Each takes `--help` for its full flag set.

```bash
grove config show | jq '.worktree.root_template'   # the resolved cascade for this repo, as JSON
grove config add-project /path/to/other-project    # register a repo for host wide views
grove config init --with-onboarding                # write .grove/config.json, install skills, register MCP
grove config schema --stdout                       # the JSON Schema, to disk without the flag
grove skills install -t user --agent claude        # Grove skills for claude | codex | all
grove mcp install --target project --agent claude  # this install as an MCP server
grove init devcontainer --force                    # a default .devcontainer/devcontainer.json
grove doctor --json                                # container requirements, as a table without the flag
grove version
grove debug                                        # every resolved path and whether config loaded
```

`grove init devcontainer` and `grove doctor` serve [Container Workspaces](features-containers.md).

### `grove daemon serve`

Runs the daemon behind the [web dashboard](use-webapp.md). See the [systemd user service](use-webapp.md#always-on-with-systemd).

```bash
grove daemon serve              # 127.0.0.1:7421
grove daemon serve --port 7777
```

`--host` defaults to loopback on purpose, see [the security model](use-auth.md#the-security-model). `--port 0` picks a free one and `--print-port` prints it once listening.

### `grove auth`

Manages pairing requests and active sessions. See [authentication](use-auth.md).

```bash
grove auth pending                 # requests waiting for approval, with their codes
grove auth approve <challenge-id>  # or deny
grove auth sessions                # active sessions
grove auth revoke <session-id>
```

## `GROVE_DEBUG=1`

Sets standard error logging to `DEBUG` for one command while standard output stays pipeable JSON.

```bash
GROVE_DEBUG=1 grove ls
```

## Tab completion

Install completion for your shell.

- `grove completions install` finds a directory without editing an rc file.
- Zsh uses the first writable `fpath` directory. Bash and fish autoload normally.
- `grove completions show --shell zsh` prints a script for dotfiles.
- Completion omits unavailable values.

```bash
grove completions install          # detects zsh, bash or fish
exec $SHELL                        # start a new shell to pick it up
```

### What completes

- Every workspace argument completes to the ids in this repo with their title and branch.
- `--agent` completes to your resolved config, `--model` to that agent's catalog, `--checkout`, `--track` and `--base` to local and remote branches, `--cwd` to the configured working directories, and `--resume-session` to recorded session ids.
- `grove phase` completes both the six phase names and workspace ids, `--ticket` and `grove tickets detach` complete attached tickets, `grove agent` verbs complete the agents live in the container, and `grove auth` verbs complete pending challenges and active sessions.

### If nothing happens when you press ++tab++ { #if-nothing-happens-when-you-press-tab }

Answering a completion means starting `grove`, about a second on a typical host, and some zsh completion frameworks give a completer far less than that and silently drop it.

- The common case is [zsh-autocomplete], whose default budget is 0.4 seconds. Raise it in `~/.zshrc` with the line below.
- Note the single trailing colon. `:autocomplete:` is the exact context the setting is read from, and `':autocomplete:*'` does not match it.
- To tell this apart from an install problem, run the round trip by hand with the command below.
- Candidates printed means Grove is fine and the shell is discarding them. Nothing printed is a real fault worth reporting.

```zsh
zstyle ':autocomplete:' timeout 3
```

```bash
time ( env _TYPER_COMPLETE_ARGS="grove show " _GROVE_COMPLETE=complete_zsh grove )
```

[zsh-autocomplete]: https://github.com/marlonrichert/zsh-autocomplete

## See also

- [Agent activity and sessions](features-activity.md).
- [Status semantics](features-status.md) and [Task phase](features-status.md#the-third-axis-task-phase).
- [Ticket providers](features-ticket-providers.md).
- [Configuration cascade](features-cascade.md) and [Configuration reference](configure-reference.md).
- [Authentication and pairing](use-auth.md) and [Web dashboard](use-webapp.md).
