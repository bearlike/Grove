# grove.core.slack — start and steer workspaces from a Slack thread

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

The Slack face of [issue-ops](../issueops/CLAUDE.md): a mention (or a DM, or the message shortcut) creates a workspace with the conversation as its prompt, thread replies steer it, and the agent's replies come back into the thread. Off by default (`cfg.slack`). Built to match Devin's Slack integration (docs.devin.ai/integrations/slack, read 2026-11-12) in shape, and deliberately not in breadth.

## The atoms

- `vocabulary.py` is every Slack word Grove sends or matches: event and payload types, the shortcut's `callback_id` (which must equal the manifest in `docs/features-slack.md`), the `!` keywords, reaction names, Slack's size limits, and the two lifecycle lines. Nothing else spells one.
- `events.py` narrows Slack JSON into frozen dataclasses, once. `MentionCommand.parse` strips the bot's mention wherever it sits, takes `!new`/`!project`/`!agent` off the front, and hands the rest to issue-ops' `CommandParser`, so `status`/`pause`/`resume`/`stop` mean the same on a tracker and in Slack. `mute`/`unmute` are the only Slack-only verbs.
- `gateway.py` is the ONLY module importing `slack_sdk`. Every call narrows to `SlackError`. `download` refuses any host outside `*.slack.com`, because the bot token rides that request and a file URL is payload data.
- `context.py` reads the conversation (whole thread for a threaded mention, `context_messages` of channel history for a top-level one) and renders the prompt. Over the 10,000-char create limit it drops the OLDEST messages first.
- `router.py` is the inbound policy, `relay.py` the outbound, `threads.py` the durable map between them, `bridge.py` the one factory the daemon holds.

## Decisions worth not re-litigating

- **Socket Mode, not the Events API.** Socket Mode dials out over a websocket, so a loopback-only daemon takes mentions with no public endpoint and no tunnel. This is the same constraint that pushed issue-ops to polling; Slack happens to offer the outbound shape natively.
- **Devin's shape: few updates.** 👀 on pickup, a configurable working reaction (the spinner) on the message being worked on, then the agent's reply when the turn ends. No tool calls, no progress posts, no paused/resumed lines. The only non-reply posts are "session ended" (kill) and "session hit an error". A custom animated emoji in `working_reaction` is the spinner. Slack has no other animation a bot can show in a channel thread.
- **A reply is posted when the primary session settles AND `FinalResult.is_complete`**, deduped by the reply's `message_id` stored in the thread record. That is exact, so no debounce: a restart re-reads the same id and posts nothing. `WorkspaceManager.final_result` was added for this. It resolves the transcript exactly like `primary_transcript`.
- **A thread reply to an open question goes through `answer_question`**, never `send_message`. Typing prose into a live picker is the failure `answer_question`'s docstring describes. The thread record carries the batch's answer-back id (`asked`) and count (`awaiting`). If the question is no longer pending, the reply falls back to a plain message.
- **One thread runs one message at a time** (a per-thread lock in the router). A reply that lands while its workspace is being created waits for the binding instead of creating a second workspace.
- **Dedupe on `(channel, ts)` with issue-ops' `RecentKeys`.** One message arrives twice, as `app_mention` and as `message`. Edits (`message_changed`) are never acted on, for the same reason issue-ops ignores `edited` comments.
- **Closed until set, on all three axes.** `allowed_users`, `allowed_channels` and the project (`default_project`/`channel_projects`) each refuse when empty, because a workspace runs an agent with the host's credentials. `"*"` (`SlackConfig.ANY`) is the only way to open an allow list, and both lists share one rule (`SlackConfig._admits`). An unlisted sender gets one reply naming their member id. An unlisted conversation names its id to an allowed sender only. `!project` picks among `SlackConfig.projects`, never the registry's known roots, so it cannot widen what Slack reaches.
- **Two config scopes, split by trust.** Access and project choice come from the daemon's `slack` section (`self._cfg`), before any project is known. Launch options (`slack.workspace`) and `prompt_template` come from the chosen project's merged config (`manager.config.slack`), the way issue-ops reads `mgr.config.issueops`. A committed repo config can therefore shape its own sessions but never grant itself access. Do not read an access field from the project's copy.
- **`slack.workspace` only forwards.** Each set field goes onto the `CreateWorkspaceRequest` under the same name `defaults` and the request use. Unset stays `None`, so `LaunchResolver` answers it from the project's `defaults`. `branch_mode` alone has a Slack default (`auto`): the saved default may be `root`, and concurrent Slack sessions must not share the operator's checkout by accident.
- **Tokens come from `env_file`/`env_command` (the shared `EnvSourceConfig` seam) overlaid on the daemon's environment.** `slack` is registered in `ExclusiveGroups.GROUPS` and `CommittedEnvSource.SECTIONS`, so a committed layer cannot run a command.

## Deliberately not built (Devin has them)

Code channels (one channel per session), proactive channel watching, `!ask` quick answers, mode keywords, two-way sync of web-composer messages into the thread, sleep-on-quiet, and email-based user linking. Each is a separate feature with its own owner. None is needed for the mention-to-answer loop.
