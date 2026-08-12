# Agent telemetry and tracing

## One session, one trace

Agents now act on staging and production unattended, and off-the-shelf harnesses
leave no record you can hold them to. Grove is an OpenTelemetry gateway in front
of them, so each turn arrives as one trace in [LangFuse](https://langfuse.com) or
any OTLP backend: what ran, where, at what cost, and where the time went.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/telemetry-trace.png" alt="One Claude Code turn as a trace: an agent-turn root with its cost and duration, nested model generations, and a span for each Bash, Skill, WebFetch and WebSearch call, with GenAI and Grove attributes on the selected span" /></div>
  <figcaption class="ms-shot__body">One turn. Every generation, every tool call, the model, the tokens and the cost.</figcaption>
</figure>

A harness exporting its own spans gives you a flat pile that cannot say which
repository it ran in. Grove stamps identity on every span, pins one vocabulary
across agents, and rebuilds the parent and child shape a raw export throws away.

## Turn it on

Telemetry ships in the `telemetry` extra, which `grove[all]` includes. Point the
config at the environment variables that carry your credentials. Grove reads the
variable *names* from config and the values from the environment, so no secret
is ever written to a config file:

```json
{
  "telemetry": {
    "enabled": true,
    "host_env": "LANGFUSE_HOST",
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY"
  }
}
```

Use `telemetry.env_file` to load those variables from a file, or
`telemetry.env_command` to fetch them from a secrets manager at startup. With
`enabled: false`, which is the default, the exporter never loads and the packages
sit inert.

## What Grove puts on a span

A raw harness span knows the model and the token counts. It does not know the
workspace, the branch, or the ticket, because the harness was never told. Grove
composes that identity into `OTEL_RESOURCE_ATTRIBUTES` when it launches the
agent, so every span the process emits carries it:

| Attribute | What it answers |
| --- | --- |
| `grove.workspace.id`, `grove.repo`, `grove.project` | Where did this run? |
| `grove.branch`, `grove.base_branch`, `grove.worktree` | Against what code? |
| `grove.agent.kind`, `grove.agent.version` | Which harness, which version? |
| `grove.runtime`, `grove.placement` | On the host, or in a container? |
| `grove.ticket.ids` | Which tickets was it attached to? |
| `langfuse.session.id` | The join key, see below. |

That last one is the hinge. Grove emits its own spans and the agent emits its
own, from two different processes that share no context. Both carry the same
`langfuse.session.id`, so LangFuse files them under one session rather than two
unrelated traces.

## Receiving the harness's own traces

Claude Code and Codex can export OTLP directly. Grove can be the collector they
export to, which lets it rewrite what arrives before it lands:

```json
{
  "telemetry": {
    "receiver": { "enabled": true, "path": "/otlp" }
  }
}
```

The daemon serves `POST /v1/traces` under that path, in protobuf or JSON. Spans
queue and drain on their own worker pool, so a burst from a twenty agent fleet
cannot stall the dashboard, and a full queue sheds through OTLP's own
`partial_success` rather than dropping work silently.

Arriving traces are grouped by trace id and handed to the first transform that
claims them. The Claude Code transform finds the turn root, reparents the
sub-agent spawn tree onto it, flags errored tool calls, and rewrites names into
Grove's vocabulary. Anything unclaimed passes through untouched.

The receiver is off by default and the daemon binds to loopback.

Once traces land, the usual questions become queries: which models the team
runs, where time goes between model wait and tool work, and what a change cost.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/telemetry-dashboard.png" alt="A LangFuse dashboard over Grove traces: trace, generation and observation latency percentiles side by side, above a model latency chart comparing ten models over three days" /></div>
  <figcaption class="ms-shot__body">Latency percentiles by trace, generation and observation, and per model over time.</figcaption>
</figure>

## One exporter, never two

An agent with `OTEL_EXPORTER_OTLP_ENDPOINT` already set in your shell exports to
your collector while Grove exports the same session elsewhere. Two partial
traces, no error.

Grove reserves the OTLP exporter variables for any agent it launches, listed
explicitly in `telemetry.reserved_env` rather than matched by prefix, so
`OTEL_RESOURCE_ATTRIBUTES` and `TRACEPARENT` survive. A displaced value logs one
warning naming the variable, never its contents.

## The vocabulary

Names and attributes follow the OpenTelemetry GenAI semantic conventions, so a
Claude session and a Codex session are comparable rather than merely adjacent.
Grove pins those keys as constants and a test asserts they still match the SDK,
because the upstream module is not yet stable.

## Backfilling history

`grove usage backfill --telemetry` replays sessions that predate telemetry.
Exporting old sessions writes off this machine, so it is gated three ways: the
`--telemetry` flag, an explicit `--yes`, and the profiles named in
`telemetry.backfill.profiles`. `--settled-minutes` skips sessions still being
written, and `--dry-run` plans without writing. Without `--telemetry` the command
only rebuilds the local projection.

## See also

- [Agent activity and sessions](features-activity.md): the live half of the
  same signal.
- [Configuration reference](configure-reference.md): every `telemetry` key.
- [Agents](configure-agents.md): declaring an agent's `kind`.
