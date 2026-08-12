"use client";

import { CheckIcon, CircleDashedIcon, CircleDotIcon, OctagonAlertIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { RelativeTime } from "@/components/grove/relative-time";
import type { PhaseView } from "@/lib/grove/api";
import type { TicketRollup } from "./selectors";

/**
 * The six task phases, in the order an agent walks them. Kept in one array
 * because the wire's `index` is a position within exactly this sequence.
 */
const PHASES = ["scoping", "planning", "implementing", "verifying", "delivering", "done"] as const;

type StepState = "complete" | "current" | "ahead";

/**
 * One tone table, never a call-site choice (section 6). The four-tone `ui/badge`
 * taxonomy applied to progress: `default` is the loudest mark and belongs to
 * the single live step; `secondary` is the quiet, settled mark for what is
 * behind the agent; `outline` is the quietest neutral mark for what has not
 * been reached. `ui/badge`, not `assistant-ui/badge` — canonical for Grove code
 * (#489).
 */
const TONE: Record<StepState, "default" | "secondary" | "outline"> = {
  complete: "secondary",
  current: "default",
  ahead: "outline",
};

/** The same glyph ramp `PhaseBadge` already uses, so shape — not colour alone —
 * carries complete/current/ahead (design-system.md §4.7). */
const GLYPH: Record<StepState, LucideIcon> = {
  complete: CheckIcon,
  current: CircleDotIcon,
  ahead: CircleDashedIcon,
};

/**
 * The task axis: what the agent says it is doing about the job, as distinct
 * from what tmux and the session say about the process.
 *
 * The agent reports this by writing a file in its worktree — a containerized
 * agent can reach no API — so a workspace whose agent has reported nothing
 * carries no phase at all and this renders nothing rather than a fake step 0
 * (the caller renders the empty state instead).
 *
 * A CHECKPOINT TRACK, not a plain progress bar: chips joined by connectors read
 * as one sequence the way six unrelated badges did not, but the meter never
 * treats "further along" as "better" — see `stateOf` for why a backward report
 * renders exactly like forward progress, and `activeIndexOf` for why a skipped
 * phase is not a lie.
 *
 * Neither vendored `elements` stepper fits without editing it. `JobProgress`
 * is a single accumulating fill bar with no per-stage note or timestamp slot,
 * and a bar that can only grow reads a legitimate backward report as breakage.
 * `Timeline` is the closer shape — past/now/future, already told apart by fill
 * as well as hue — but its `time` field is a plain `string`, which cannot host
 * the live-updating, hover-exact `RelativeTime` this card requires; its own
 * existing caller (`changes-tab.tsx`) already gives that up for a static
 * `toLocaleString()`. So this composes the same dot-and-connector language in
 * Grove-owned markup instead of forcing the wrong prop shape.
 */
/**
 * How far the workspace has got across every ticket it holds, as one bar.
 *
 * **A PLAIN BAR HERE, and a checkpoint track below it, is the whole point of
 * having both.** A track answers "which step is this on", which only means
 * something for a single claim; asked of six tickets at once it has no answer.
 * A batch's question is "how much is left", and that is a magnitude — the one
 * case where the accumulating fill `PhaseMeter`'s own docstring rejects is the
 * right shape, because an aggregate genuinely CAN only be read as more-or-less,
 * and a backward report on one ticket among six is a small dip rather than the
 * misread-as-breakage this surface avoids for a single claim.
 *
 * The counts beside it are not decoration: a fraction alone cannot distinguish
 * *measured* progress from silence, and `ticketRollup` deliberately scores an
 * unclaimed ticket as zero. So `unreported` is printed wherever it is non-zero —
 * without it a workspace that has reported nothing at all shows a confident 0%
 * that reads as "no progress" when the honest claim is "nobody has said".
 */
export function TicketRollupMeter({ rollup }: { rollup: TicketRollup | null }) {
  if (!rollup) return null;
  const percent = Math.round(rollup.fraction * 100);

  return (
    <div className="flex flex-col gap-2" data-testid="ticket-rollup">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-content-secondary">
          <span className="font-medium text-content-primary tabular-nums">
            {rollup.done}/{rollup.total}
          </span>{" "}
          tickets done
        </span>
        <span className="text-xs tabular-nums text-content-tertiary" data-testid="rollup-percent">
          {percent}%
        </span>
      </div>

      <Progress value={percent} aria-label="Completion across every attached ticket" />

      {/* Only the facts that are true right now get a chip. A zero is a fact
          nobody needs — "0 blocked" spends a reader's attention to tell them
          nothing, and a row of zeroes is how a summary stops being read. */}
      {(rollup.blocked > 0 || rollup.unreported > 0) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {rollup.blocked > 0 && (
            <Badge variant="destructive" data-testid="rollup-blocked">
              <OctagonAlertIcon aria-hidden />
              {rollup.blocked} blocked
            </Badge>
          )}
          {rollup.unreported > 0 && (
            <Badge variant="outline" data-testid="rollup-unreported">
              {rollup.unreported} not reported
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

export function PhaseMeter({ phase }: { phase: PhaseView | null }) {
  if (!phase) return null;
  const reached = activeIndexOf(phase);

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="phase-meter"
      data-phase={phase.phase}
      data-blocked={phase.blocked || undefined}
    >
      {/* BLOCKED LEADS THE TRACK RATHER THAN EXTENDING IT. It is a flag across
          the axis, not a seventh checkpoint, so it sits OUTSIDE the `ol` — a
          seventh `li` would read as a step the agent is meant to reach, and
          anything appended after `done` reads as a step it already passed.
          Leading it puts the two facts in the order a reader wants them: that
          the work is stuck, then where it is stuck.

          §4.7 — THE WORD IS THE CARRIER AND THE COLOUR ONLY AGREES WITH IT.
          `blocked` is spelled out and the octagon is a stop sign's shape, so
          the mark survives greyscale and dichromatic vision with the hue gone;
          `--destructive` on its own measurably cannot (12/255 against
          `--success` in greyscale).

          §6 — the tone budget, reasoned rather than assumed. The track is one
          composite mark, not six competing chips: its tones are POSITIONS in a
          single sequence and every chip prints its own word, which is why the
          design system already names this meter's vocabulary as the model to
          copy. Against that sequence `blocked` is the row's only `destructive`
          and the current step keeps the only `default` — one loudest mark for
          the object, exactly as a fleet card spends `default` on status while a
          waiting agent takes `destructive`. */}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {phase.blocked && (
          <Badge
            variant="destructive"
            title={`The agent reports it cannot make progress in ${phase.phase}.`}
            data-testid="phase-blocked"
          >
            <OctagonAlertIcon aria-hidden />
            blocked
          </Badge>
        )}
        <ol className="flex flex-wrap items-center" aria-label="Task phase">
          {PHASES.map((name, index) => {
            const state = stateOf(index, reached);
            const Glyph = GLYPH[state];
            return (
              <li key={name} className="flex items-center">
                {index > 0 && <span aria-hidden className="mx-1 h-px w-3 shrink-0 bg-border" />}
                <Badge
                  variant={TONE[state]}
                  aria-current={state === "current" ? "step" : undefined}
                  data-done={state === "complete" || undefined}
                  data-current={state === "current" || undefined}
                >
                  <Glyph aria-hidden />
                  {name}
                </Badge>
              </li>
            );
          })}
        </ol>
      </div>
      {phase.note && (
        <p className="flex flex-wrap items-baseline gap-x-1.5 text-sm">
          <span>{phase.note}</span>
          <span className="text-xs text-muted-foreground">
            <RelativeTime iso={phase.updated_at} />
          </span>
        </p>
      )}
    </div>
  );
}

/**
 * Where one step falls relative to the reported position.
 *
 * Grove keeps only the agent's LATEST claim — the phase file is overwritten in
 * place, never appended — so "before the reported index" is read as PASSED
 * THROUGH, not as "individually visited". An agent that jumps straight to
 * `implementing` is not being misrepresented by `scoping` reading complete:
 * completeness here means "at or before the reported position", which is all
 * the wire actually asserts. `PHASE_ORDER` is linear and convergent but never
 * enforced monotonic (`grove.core.phase`), so this has no way to know — and no
 * need to know — whether a skipped phase genuinely happened.
 *
 * A BACKWARD report — `verifying` back to `planning` because the design turned
 * out wrong — renders exactly like any other position: this is a pure function
 * of the current index, so a step that used to read complete simply reads
 * ahead again. Nothing marks the transition itself, because the report is
 * correct, not a fault to flag.
 */
function stateOf(index: number, reached: number): StepState {
  if (index < reached) return "complete";
  if (index === reached) return "current";
  return "ahead";
}

/**
 * `done` completes the list, so nothing is "current" — every chip reads
 * complete. For any other phase the wire's `index` is the position being
 * worked.
 *
 * Past-the-end comes from `phase.total`, THE WIRE'S OWN COUNT, not from this
 * file's array length: `total` exists precisely so a client does not pin a
 * second copy of the vocabulary, and a meter that counted its own labels would
 * be that copy. The names above are unavoidable — the wire sends one phase word,
 * not six — so the count at least stays where the daemon owns it.
 */
function activeIndexOf(phase: PhaseView): number {
  return phase.phase === "done" ? phase.total : phase.index;
}
