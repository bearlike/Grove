import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { SectionCard } from "@/components/grove/card";
import { BranchLabel, PROJECT_MIN_WIDTH, ProjectLabel } from "@/components/grove/entity";
import { baseBranchOf } from "@/lib/grove/adapters";
import {
  AgentStateBadge,
  PhaseBadge,
  RuntimeBadge,
  StatusBadge,
  TicketChip,
  TodoBadge,
} from "./badges";
import { agentStateOf, currentTaskOf } from "./filter";
import type { WorkspaceActivity } from "./types";

/**
 * One workspace at a glance.
 *
 * The reading order is the answer to three questions in the order people ask
 * them: what is this (project, title, branch), what is happening (the three
 * axes), and how much has it done (the counters). Everything past that — the
 * pane, the transcript, the controls — lives on the workspace route, one click
 * away.
 *
 * The repo is a LINE on the card rather than a section heading above it. The
 * wall is flat and reverse-chronological, so the card has to say where it came
 * from itself; grouping said it once per section and spent a whole heading on
 * every repo that happened to be empty.
 *
 * It is a `SectionCard` like every other card in the app, which is not a
 * stretch: its header asks the same three questions the shared header answers —
 * what is this (the title), what is it about (repo and branch), and what state
 * is it in (the status badge). The icon slot takes the agent's own brand mark,
 * so the thing that identifies a fleet card is the harness running in it.
 *
 * The counters that used to sit in a `CardFooter` are the body's last row
 * instead. A footer under a card that already has a tinted header and a rule
 * gave the summary three horizontal bands to read; the counters are secondary
 * to the badges above them, and muted type says that without another divider.
 *
 * THE BODY IS FOUR NAMED REGIONS, ALWAYS IN THIS ORDER, EACH WRAPPING ON ITS
 * OWN. What this replaced was one `flex-wrap` row holding the agent name, four
 * counters and every ticket — so a five-ticket workspace flowed its chips out
 * of the middle of a sentence of figures and down three ragged lines, while a
 * one-ticket workspace was a different shape entirely. Wrapping is a property
 * of a SET, and that row was three sets pretending to be one.
 *
 *   marks    the three status axes — chips, wrapping as chips
 *   task     the agent's current sentence — prose, wrapping as prose
 *   error    the failure, when there is one
 *   ledger   the counters, then the tickets: two rows, two independent wraps
 *
 * A region with nothing in it renders nothing, so the ORDER is invariant while
 * the height is not — which is the whole point. `mt-auto` on the ledger is what
 * makes that visible: `SectionCard`'s body is `flex-1` and `CardGrid` stretches
 * every card in a row to the tallest, so pinning the counters to the bottom
 * lines them up ACROSS the row. Before, a card with a one-line task and a card
 * with five tickets put their figures at two unrelated heights and the eye had
 * to find each one.
 */
export function WorkspaceCard({
  workspace,
  repoName,
}: {
  workspace: WorkspaceActivity;
  repoName: string;
}): React.ReactNode {
  const { state } = workspace;
  const task = currentTaskOf(workspace);
  const base = baseBranchOf(state);

  return (
    <SectionCard
      data-testid="workspace-card"
      data-workspace-id={state.id}
      data-attention={workspace.needs_attention}
      data-repo-name={repoName}
      icon={<AgentMark agentName={state.agent_name} />}
      title={
        <Link href={`/w/${state.id}`} title={state.title}>
          {state.title}
        </Link>
      }
      description={
        // Typed entities, so no middot: the glyphs already say where one ends
        // and the next begins. The base branch keeps its arrow because "←" is
        // the RELATION between two branches, not a separator between fields —
        // which is exactly why BOTH go when there is no base. An arrow with
        // nothing after it points at an absence and asks the reader to name it.
        <span className="flex min-w-0 items-center gap-2">
          {/* Same floor the rail row keeps, and for the same reason: this
              description line has two or three entities sharing it, and
              without a guarantee the project — usually the longer string —
              is the one a proportional shrink gives up first. */}
          <ProjectLabel name={repoName} className={PROJECT_MIN_WIDTH} />
          <BranchLabel name={state.branch} />
          {base ? (
            <>
              <span className="shrink-0 text-content-tertiary">←</span>
              <BranchLabel name={base} />
            </>
          ) : null}
        </span>
      }
      action={<StatusBadge status={state.status} />}
    >
      {/* REGION 1 — MARKS. THREE, and each says something the other two cannot:
          what the agent is doing, how far through the task it is, and what it
          is isolated by. The fourth was `AttentionBadge`, which said nothing new
          — `needs_attention` is derived from the very state the first badge
          prints, so a workspace waiting on a human read "waiting for you" and
          "needs you" side by side. The signal did not weaken: it moved into
          `AGENT_TONE`, which now spends `destructive` on exactly the states the
          daemon calls attention states.

          AT MOST ONE OF THESE IS EVER TONED, which is what lets the accent be
          spent here at all. `working`/`starting` take amber from `agentAccent`
          and `waiting`/`blocked`/`error` take `destructive` — never both, since
          they are values of one union — and the phase and runtime chips beside
          them are `outline` by table. The header's status badge is the object's
          one `default`, and it sits in a different band. */}
      <div className="flex flex-wrap items-center gap-1.5" data-testid="card-marks">
        <AgentStateBadge state={agentStateOf(workspace)} />
        <PhaseBadge phase={workspace.phase} />
        <RuntimeBadge runtime={state.runtime} fallbackReason={state.runtime_fallback_reason} />
      </div>

      {/* REGION 2 — TASK. A sentence, so it wraps as prose and clamps rather
          than truncating: §8's rule that clipping a sentence deletes the
          predicate the row exists for. */}
      {task ? (
        <p className="line-clamp-2 text-sm text-content-secondary" data-testid="card-task">
          {task}
        </p>
      ) : null}

      {/* REGION 3 — ERROR. A SIGNAL plus an explanation, and this had neither
          part marked — a failed workspace printed its reason in the same neutral
          as the task above it, so the card's worst news was its quietest line. */}
      {state.error_detail ? (
        <p
          className="flex items-start gap-2 text-sm text-content-secondary"
          role="alert"
          data-testid="card-error"
        >
          <TriangleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
          <span className="line-clamp-2">{state.error_detail}</span>
        </p>
      ) : null}

      {/* REGION 4 — LEDGER: what this workspace has ACCUMULATED, in two rows
          that wrap independently of each other and of everything above.
          `mt-auto` pins it to the card's floor so a row of cards lines its
          figures up; see the anatomy note in this file's header. */}
      <div className="mt-auto flex flex-col gap-2">
        <div
          className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-xs text-content-tertiary"
          data-testid="card-metrics"
        >
          <span className="truncate">{state.agent_name}</span>
          {/* No `text-success`/`text-destructive` on these, deliberately: §4.7
              forbids a coloured figure with no word beside it, and `+12 −4`
              carries a sign, not a word. The colour on this row is spent on the
              one figure that has a glyph AND a bounded denominator. */}
          <span className="tabular-nums" title="lines added / removed in this worktree">
            +{workspace.diff_added} −{workspace.diff_removed}
          </span>
          <span className="tabular-nums" title="commits ahead of / behind the base branch">
            ↑{workspace.base_ahead} ↓{workspace.base_behind}
          </span>
          {workspace.todo ? <TodoBadge todo={workspace.todo} /> : null}
        </div>

        {state.ticket_refs.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="card-tickets">
            {state.ticket_refs.map((ticket) => (
              <TicketChip key={`${ticket.provider}#${ticket.id}`} ticket={ticket} />
            ))}
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}
