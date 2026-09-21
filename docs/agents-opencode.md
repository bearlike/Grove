# OpenCode

## Control an OpenCode session from Grove

OpenCode ships a headless HTTP server. Grove starts `opencode serve` on loopback, creates a session against it and follows that session's events, so you steer the work from the web dashboard, the TUI or MCP.

- **The server is Grove's, and so is the session.** Grove spawns the server, reads the address it prints and owns the session it creates on it.
- **A message is confirmed by the reply.** The prompt route answers with the assistant turn itself, so delivery is evidence rather than an acknowledgement.
- **Interrupt aborts the running turn.** A long tool call stops without ending the session.
- **Approvals come to you.** Grove answers a pending permission request with once, always or reject.
- **Compact and commands are the server's own verbs.** Grove asks OpenCode to summarize the session or run a discovered command rather than typing into a terminal.
- **Models come from the server.** Grove reads the providers OpenCode itself reports, so the picker offers what this installation can actually run.

## One server, many sessions

This is the difference from the two adapters that speak over a pipe. A pipe is one session by construction. An OpenCode server hosts many, so Grove filters its event stream to the session it owns and ignores every frame belonging to another.

That shapes two things you can see. Grove reads the session your workspace created and no other, even when the same server is busy elsewhere. And the transcript lives in one shared database rather than a file per session.

## The database is read only

OpenCode keeps every session on the host in a single SQLite database. Grove opens it read only and never writes to it, so your history stays OpenCode's to manage.

The practical consequence is that a paused or killed workspace does not take its transcript with it. The record stays where OpenCode put it.

## Known limitations

- **A failed tool can leave the turn unfinished.** Measured on OpenCode 1.18.18, a tool call that fails stays reported as running and the turn does not settle. Interrupt is the way out.
- **Grove targets the stable routes.** The newer `/api` routes accept a custom provider and then fail after the request has already succeeded, so Grove does not use them.
- **Permission policy stays OpenCode's.** Grove answers a prompt when one arrives. It does not set the policy that decides whether one arrives at all.

## Configuration

```json title=".grove/config.json"
{
  "agents": [
    { "name": "opencode", "command": "opencode", "kind": "opencode" }
  ]
}
```

Pick a model and an agent per workspace with `GROVE_OPENCODE_MODEL` and `GROVE_OPENCODE_AGENT` on the agent's own `env`. Leave both unset and Grove uses the model and default agent from your OpenCode configuration.

```json title=".grove/config.json"
{
  "agents": [
    {
      "name": "opencode-review",
      "command": "opencode",
      "kind": "opencode",
      "env": { "GROVE_OPENCODE_AGENT": "plan" }
    }
  ]
}
```

A model reads as `provider/model`, matching OpenCode's own configuration. Grove forwards whatever you name without validating it.

## See also

- [Agents](configure-agents.md): choosing between native, headed and headless.
- [Claude Code](agents-claude-code.md) and [Codex](agents-codex.md): the other native adapters.
- [Agent activity and sessions](features-activity.md): what the transcript unlocks.
