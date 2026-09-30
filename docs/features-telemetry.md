# Agent telemetry and tracing

## Trace what your agents did

Every agent turn becomes a trace your team can review. Rate answers in Grove with a thumb and optional reasons or notes. Those annotations stay on the Langfuse trace for human review and automated LLM judge workflows.

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/telemetry-trace.png" alt="One Claude Code turn as a trace: an agent-turn root with its cost and duration, nested model generations, and a span for each Bash, Skill, WebFetch and WebSearch call, with GenAI and Grove attributes on the selected span" />
        <figcaption>One turn. Every generation, every tool call, the model, the tokens and the cost.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/telemetry-dashboard.png" alt="A LangFuse dashboard over Grove traces: trace, generation and observation latency percentiles side by side, above a model latency chart comparing ten models over three days" />
        <figcaption>Latency percentiles by trace, generation and observation, and per model over time.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

## One session, one trace

Grove is an OpenTelemetry gateway in front of your agents, so each turn arrives as one trace in [LangFuse](https://langfuse.com) or any OTLP backend.

- Every trace says what ran, where, at what cost, and where the time went between model wait and tool work.
- A harness exporting its own spans gives you a flat pile that cannot say which repository it ran in. Grove stamps identity on every span and rebuilds the parent and child shape a raw export throws away.
- Names and attributes follow the OpenTelemetry GenAI semantic conventions, so a Claude session and a Codex session are comparable rather than merely adjacent.
- Once traces land, the usual questions become queries. Which models the team runs, what a change cost, which tool calls eat the time.

## What Grove puts on a span

A raw harness span knows the model and the token counts. It does not know the workspace, the branch or the ticket, because the harness was never told.

| Attribute | What it answers |
| --- | --- |
| `grove.workspace.id`, `grove.repo`, `grove.project` | Where did this run? |
| `grove.branch`, `grove.base_branch`, `grove.worktree` | Against what code? |
| `grove.agent.kind`, `grove.agent.version` | Which harness, which version? |
| `grove.runtime`, `grove.placement` | On the host, or in a container? |
| `grove.ticket.ids` | Which tickets was it attached to? |
| `langfuse.session.id` | The join key. |

- Grove composes that identity into `OTEL_RESOURCE_ATTRIBUTES` when it launches the agent, so every span the process emits carries it.
- Grove's spans and the agent's come from two processes that share no context. Both carry the same `langfuse.session.id`, so LangFuse files them under one session rather than two unrelated traces.
- The same identity is written flat as metadata, `grove_workspace_id`, `grove_repo`, `grove_branch` and the rest, so a cohort can be scoped to one repo or split by harness.

## Shell calls, in one shape you can grade

A shell command is where an agent touches the machine, so it is the tool call most worth reviewing, and no two harnesses spell it the same way. Grove normalizes every one into a single observation shape, a shipping label that reads the same whatever box it came in.

- `input` carries a stable `command` string plus the harness's own `argv` and `arguments`. `output` carries `content`, a `truncated` flag and `exit_code` when one was recorded.
- The facts you filter on are flat metadata. `grove_tool_category` is always `shell`, `grove_shell_outcome` is `succeeded`, `failed`, `pending` or `unknown`, and `grove_tool_source` says whether Grove replayed the transcript or re spelled the harness's own span.
- To grade them, create an LLM judge evaluator in LangFuse, filter on `grove_tool_category` equal to `shell`, and map its variables to `input` and `output`. Grove records facts and runs no evaluation of its own.
- The shape refuses to guess. Success is claimed only from a recorded exit code of zero, a cancelled call is not distinguishable from a failed one, a background call's result is its launch handle, and a payload over 4000 characters says it is `truncated`.

## Rate a turn, and the rating lands on its trace

A person watching an agent knows at once when a turn wasted steps, and that judgement is worth more than any heuristic, so the web app lets them record it where the trace already lives.

- Every turn's final answer carries Copy, thumbs up and thumbs down. There is no regenerate, because a Grove agent keeps working in its own terminal and a rerun would promise something the daemon cannot do.
- Either thumb opens a short follow-up asking why, with an optional free-text note. A thumbs down lists what cost the turn time, such as unnecessary actions or testing, slow commands run without need and a cluttered context. A thumbs up lists what made it efficient, such as reaching the goal directly with no wasted steps.
- The rating becomes Langfuse scores on the very trace that turn exported: a `user-feedback` boolean with the note as its comment, then one `user-feedback-reason` category per complaint or one `user-feedback-praise` category per compliment. Voting again replaces the previous rating, and flipping a vote withdraws the reasons the old verdict gave.
- The thumbs appear only when Langfuse is configured, so a rating always has somewhere to go. The scores are human annotations, which makes them ground truth for an LLM judge you calibrate later.

## Turn it on

Telemetry ships in every `grove-factory` install and stays off until you enable it. Grove reads variable names from config and values from the environment, so no secret is ever written to a config file.

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

- `telemetry.env_file` loads those variables from a file, or `telemetry.env_command` from a secrets manager. A daemon reads its environment once, so a file is what survives a key rotation.
- Grove reserves the OTLP exporter variables for every agent it launches, listed in `telemetry.reserved_env`, so a stray `OTEL_EXPORTER_OTLP_ENDPOINT` in your shell cannot split one session into two traces. A displaced value logs one warning naming the variable.

## Receiving the harness's own traces

Claude Code and Codex can export OTLP directly, and Grove can be the collector they export to, rewriting what arrives before it lands.

```json
{
  "telemetry": {
    "receiver": { "enabled": true, "path": "/otlp" }
  }
}
```

- The daemon serves `POST /v1/traces` under that path, off by default and bound to loopback. A burst from a twenty agent fleet cannot stall the dashboard, and a full queue sheds through OTLP's own `partial_success`.
- The Claude Code transform finds the turn root, reparents the sub agent tree onto it, flags errored tool calls and rewrites names into Grove's vocabulary. Anything unclaimed passes through untouched.

## Backfilling history

`grove usage backfill --telemetry` replays sessions that predate telemetry.

Exporting old sessions writes off this machine, so it needs the `--telemetry` flag, an explicit `--yes`, and the profiles named in `telemetry.backfill.profiles`. `--dry-run` plans without writing, and without `--telemetry` the command only rebuilds the local projection.

## Registering the feedback rubrics

Langfuse only accepts a human annotation that names the score config it grades against. Create all three configs once per project before anyone rates a turn. Until they exist, a rating fails with a message that names the missing config. With the [Langfuse CLI](https://www.npmjs.com/package/langfuse-cli) and the same credentials Grove exports with:

```bash
langfuse api score-configs create --name user-feedback --data-type BOOLEAN \
  --description "Human thumbs up or down on one agent turn"

langfuse api score-configs create --body-json '{
  "name": "user-feedback-reason",
  "dataType": "CATEGORICAL",
  "description": "Why a human rated an agent turn down",
  "categories": [
    {"label": "Unnecessary actions", "value": 0},
    {"label": "Unnecessary testing", "value": 1},
    {"label": "Wasted time", "value": 2},
    {"label": "Ran slow commands without need", "value": 3},
    {"label": "Cluttered the context", "value": 4},
    {"label": "Missed the goal", "value": 5}
  ]
}'
langfuse api score-configs create --body-json '{
  "name": "user-feedback-praise",
  "dataType": "CATEGORICAL",
  "description": "Why a human rated an agent turn up",
  "categories": [
    {"label": "Reached the goal directly", "value": 0},
    {"label": "No wasted steps", "value": 1},
    {"label": "Tested only what mattered", "value": 2},
    {"label": "Clear explanation", "value": 3},
    {"label": "Stayed in scope", "value": 4}
  ]
}'
```

- The names are fixed: `user-feedback` must be `BOOLEAN`, and `user-feedback-reason` and `user-feedback-praise` must be `CATEGORICAL`. Grove finds each config by name when a rating arrives.
- The category labels must match `telemetry.feedback_reasons` and `telemetry.positive_feedback_reasons` exactly, because the daemon refuses a reason the config does not list. To change a list, set the key and give its Langfuse config the same labels. An archived config counts as missing.

```json
{ "telemetry": { "feedback_reasons": ["Unnecessary actions", "Wasted time", "Missed the goal"] } }
```

## See also

- [Agent activity and sessions](features-activity.md): the live half of the
  same signal.
- [Configuration reference](configure-reference.md): every `telemetry` key.
- [Agents](configure-agents.md): declaring an agent's `kind`.
