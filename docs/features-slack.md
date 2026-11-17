# Slack

## Start a session from a Slack thread

Mention Grove in a Slack thread and an agent starts working on it, the way a teammate would. The agent reads the conversation, works in its own workspace, and answers in the thread. Replying in the thread steers it.

## How a session looks in Slack

The thread stays quiet while the agent works. You see a reaction while it runs and the agent's answer when its turn ends.

- **Mention it.** Write `@Grove fix the login redirect` in a channel or a thread. A 👀 reaction says Grove picked it up.
- **It reads the room.** A mention inside a thread reads the whole thread. A mention at the top of a channel reads the channel's recent messages. Files shared in the conversation are copied into the workspace.
- **A spinner while it works.** The message Grove is working on carries a ⏳ reaction until the agent finishes its turn. Set `working_reaction` to a custom animated emoji, such as `loading`, for a real spinner.
- **Only the answers come back.** When a turn ends, the spinner comes off and the agent's reply is posted in the thread. Tool calls and progress stay in Grove.
- **Questions too.** When the agent asks you something, the question and its options are posted in the thread, and your reply answers it.

## Talk to it in the thread

Every plain reply in the thread goes to the same agent as a follow-up. You don't need to mention Grove again.

- **Steer.** Reply with more detail, a correction or a file. The spinner moves to your reply until the agent answers.
- **Talk around it.** Put `(aside)` or `!aside` anywhere in a message, and Grove does not forward it.
- **Mute it.** Mention `@Grove mute` to stop forwarding replies, and `@Grove unmute` to start again.
- **Run it.** Mention `@Grove status`, `pause`, `resume` or `stop`. These mean the same as on [Issue Ops](issue-ops.md).
- **Start over.** Mention `@Grove !new ...` in a thread to start a fresh session there.

## Other ways in

You don't have to mention Grove in a channel.

- **Direct messages.** A DM to Grove needs no mention. Every message starts or steers a session. A DM is a conversation like any other, so its ID must be in `allowed_channels`.
- **Message shortcut.** Open a message's **More actions** menu and pick **Start a Grove session**. The message becomes the request.
- **Pick the project.** Each channel maps to a project in config. Add `!project <name>` to a mention to pick another project your Slack config names.
- **Pick the agent.** Add `!agent <name>` to a mention to run a different configured agent.

## Who can use it

A session runs an agent on your machine with your credentials, so nothing is open until you open it. Grove acts only when the person, the conversation and the project are all named in config.

- **People.** `allowed_users` lists Slack member IDs. Empty allows nobody. A mention from anyone else gets a reply naming their member ID, so you can add them.
- **Conversations.** `allowed_channels` lists the channels and DMs Grove works in. Empty allows none. An allowed person who mentions Grove elsewhere gets a reply naming the conversation's ID. Nobody else gets a reply.
- **Projects.** A mention works in its channel's project from `channel_projects`, or in `default_project`. With neither set, Grove refuses. `!project` picks only among those.
- **Wildcard.** `"*"` in either list allows everyone or everywhere. It is the only way to open a list, and it is never the default. With `"*"` in `allowed_users`, anyone who can post in an allowed channel can run an agent with your credentials.
- **Everyone can read.** Anyone in the channel sees the thread and the agent's answers.
- **No public endpoint.** Grove connects out to Slack over Socket Mode, so the daemon stays on loopback.
- **Tokens stay out of config.** Config names the variables that hold the tokens. Grove reads them from the daemon's environment or from the section's `env_file` or `env_command`.

## What happens behind the scenes

One mention becomes one workspace. Each step below either passes or stops with a short reply in the thread.

1. **Slack delivers the message.** The daemon holds an outbound connection to Slack, and Slack sends it your mention, DM or shortcut.
2. **Grove checks access.** The conversation must be in `allowed_channels` and you must be in `allowed_users`.
3. **Grove picks the project.** It uses `!project` if you wrote one, then the channel's entry in `channel_projects`, then `default_project`.
4. **Grove reads the conversation.** A mention inside a thread reads the whole thread. A mention at the top of a channel reads the last `context_messages` messages, 20 by default. Shared files are downloaded.
5. **Grove writes the first prompt.** It fills the prompt template with your request and the conversation. The next section shows it.
6. **Grove creates the workspace.** It starts on a new branch in its own worktree, with the launch options from `slack.workspace` and the project's saved defaults. The prompt is the agent's first message and the files are its attachments.
7. **The thread is bound to the workspace.** Grove remembers the pair on disk, so later replies reach the same agent, even after a daemon restart.
8. **The answer comes back.** When the agent finishes a turn, Grove posts its final reply in the thread and takes the spinner off.

A later reply in the thread skips steps 3 to 6. It reaches the running agent as a follow-up message that starts with `<name> replied in Slack:`, with any files attached. If the agent had asked a question, the reply is delivered as its answer.

## The prompt the agent receives

The agent's first message is `prompt_template` with five placeholders filled from Slack. This is the built-in template:

```text title="Default prompt_template"
You were started from a Slack conversation in #{channel}.

{author} asked:
{command_text}

The conversation so far, oldest first:
{thread}

Slack link: {permalink}

How this session works:

- Every reply you finish is posted back into that Slack thread, so write each
  final reply for the people reading it there: lead with the answer, keep it
  short, and say what you changed and where.
- People may reply in the thread while you work; their messages arrive here as
  follow-up instructions.
- Where the request is underspecified, ask in your reply and say what you will
  assume if nobody answers. Do not stall silently.
- Stop and ask rather than take a risky or irreversible action: destroying
  data, force-pushing a shared branch, or touching anything in production.
```

| Placeholder | Filled with |
|---|---|
| `{channel}` | The channel's name. |
| `{author}` | The display name of the person who asked. |
| `{command_text}` | Your request, with the mention and any `!` options removed. |
| `{thread}` | The conversation, oldest first, each message under its author's name, with the names of any shared files. |
| `{permalink}` | A link back to the Slack message. |

What `{thread}` holds depends on where you mentioned Grove.

- **In a thread.** Every message of that thread, including the one that started it.
- **At the top of a channel.** The channel's recent messages, then your own.

Three limits apply.

- **Length.** The prompt is capped at 10,000 characters. When a conversation is longer, Grove drops its oldest messages first and keeps your request whole.
- **Files.** Up to 20 files are copied into the workspace as attachments. A file over `max_attachment_bytes` is named in the prompt with its Slack link instead.
- **Unknown placeholders.** Any other `{name}` in your template is left as written.

To change the prompt, set `slack.prompt_template` in your config, or in one project's config to change it for that project only.

## Set it up

Create a Slack app from this manifest at [api.slack.com/apps](https://api.slack.com/apps). Choose **Create New App**, then **From a manifest**.

```yaml title="Slack app manifest"
display_information:
  name: Grove
features:
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Grove
    always_online: true
  shortcuts:
    - name: Start a Grove session
      type: message
      callback_id: grove_start_session
      description: Start a Grove session on this message
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - channels:read
      - chat:write
      - commands
      - files:read
      - groups:history
      - groups:read
      - im:history
      - im:read
      - reactions:write
      - users:read
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
      - message.im
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
```

Then:

1. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`).
2. Under **Basic Information**, create an app-level token with the `connections:write` scope (`xapp-…`).
3. Put both tokens where the daemon can read them, as `GROVE_SLACK_BOT_TOKEN` and `GROVE_SLACK_APP_TOKEN`.
4. Add the `slack` section below to your user config and restart the daemon.
5. Invite Grove to a channel with `/invite @Grove`, then mention it.

```json title="~/.config/grove/config.json"
{
  "slack": {
    "enabled": true,
    "env_command": "infisical export --format=dotenv --path=/slack",
    "allowed_users": ["U012ABCDEF"],
    "allowed_channels": ["C0123WEB", "C0123API"],
    "default_project": "~/Projects/acme-web",
    "channel_projects": { "C0123API": "~/Projects/acme-api" },
    "workspace": { "agent": "claude", "model": "opus" },
    "deep_link_base_url": "http://localhost:3000"
  }
}
```

Find a member ID under a person's profile, **More**, **Copy member ID**. Find a channel ID at the bottom of the channel's details. Grove also names the ID in its reply when it refuses a person or a conversation.

## Configure it

Slack settings live in two places, and each setting is read from exactly one of them.

| Setting | Read from | What it controls |
|---|---|---|
| `enabled`, tokens, `env_file`, `env_command` | Your user config | The connection to Slack. |
| `allowed_users`, `allowed_channels` | Your user config | Who may use Grove, and where. |
| `default_project`, `channel_projects` | Your user config | Which project a mention works in. |
| `context_messages`, `max_attachment_bytes`, `working_reaction`, `deep_link_base_url` | Your user config | What Grove reads and how the thread looks. |
| `workspace` | The project's config, over yours | How the workspace is launched. |
| `prompt_template` | The project's config, over yours | The agent's first message. |

Access and project choice are read from your user config only, so a repository cannot grant itself access by committing a config file.

### Workspace options

`slack.workspace` sets how a Slack session is launched. Every field is optional.

| Field | Controls |
|---|---|
| `agent` | Which configured agent runs. `!agent` in a mention beats it. |
| `model`, `effort` | The model and reasoning effort sent to the agent. |
| `runtime`, `remote_runtime` | Where the agent runs: `host`, `container` or `remote`. |
| `branch_mode` | `auto` for a new branch and worktree, or `root` for the project's current checkout. |
| `base_ref` | The branch or ref a new branch starts from. |
| `skip_init` | Whether to skip the project's init script. |
| `brief` | Whether the agent gets Grove's first turn brief. |
| `native` | Whether the agent runs as a native session or in its terminal. |

Each option is answered by the first place that sets it:

1. `!agent` in the mention, for the agent only.
2. `slack.workspace` in the project's `.grove/config.json`.
3. `slack.workspace` in your user config.
4. The project's saved `defaults`, then your own.
5. Grove's built-in answer.

`branch_mode` is the one exception. Unset, it is `auto` and never the saved default, so a Slack session gets its own branch unless you choose `root`.

A project overrides only what it names. This project runs its Slack sessions in a container on another agent and leaves the rest to you:

```json title=".grove/config.json"
{
  "slack": {
    "workspace": { "agent": "codex", "runtime": "container" }
  }
}
```

Every field is in the [configuration reference](configure-reference.md).

## See also

- [Issue Ops](issue-ops.md) does the same from a GitHub or Gitea issue comment.
- [Push Notifications](features-notifications.md) reach your phone when an agent needs you.
- [Attachments & Annotation](features-attachments.md) explains where shared files land in the workspace.
