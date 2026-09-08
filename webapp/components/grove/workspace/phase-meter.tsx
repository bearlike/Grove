"use client";

import { useEffect, useRef, useState } from "react";
import { OctagonAlertIcon } from "lucide-react";

import { CardRegion } from "@/components/grove/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { phaseGlyph, phaseLabel } from "@/components/grove/fleet/tokens";
import {
  absoluteTime,
  durationSince,
  RelativeTime,
  useNow,
} from "@/components/grove/relative-time";
import {
  activeIndex,
  inPhaseProgress,
  reportIsStale,
  stepIsLive,
  stepState,
  type StepState,
} from "@/lib/grove/adapters/phase-progress";
import type { PhaseView, TodoProgressView } from "@/lib/grove/api";
import { cn } from "@/lib/utils";
import { rollupCoverage, rollupFormula, type TicketRollup } from "./selectors";

/**
 * The six task phases, in the order an agent walks them. Kept in one array
 * because the wire's `index` is a position within exactly this sequence.
 */
const PHASES = [
  "scoping",
  "planning",
  "implementing",
  "verifying",
  "delivering",
  "done",
] as const;

/**
 * The mark's own weight, as POSITIONS in one sequence rather than three tones
 * competing on a row. `phaseGlyph` supplies the same six silhouettes used by
 * fleet cards and rail marks; weight determines reached/current/ahead. Shape
 * carries all three states independently, so the track survives greyscale
 * (§4.7) and the hue below is only ever agreeing with it.
 */
const PHASE_LABELS: Record<
  (typeof PHASES)[number],
  { full: string; short: string }
> = {
  scoping: { full: "Scoping", short: "Scope" },
  planning: { full: "Planning", short: "Plan" },
  implementing: { full: "Implementing", short: "Build" },
  verifying: { full: "Verifying", short: "Verify" },
  delivering: { full: "Delivering", short: "Deliver" },
  done: { full: "Done", short: "Done" },
};

/**
 * The step's hue, as the SECOND carrier of a state its glyph already states.
 *
 * Reached and current both read `--primary`, which is the point: the track's
 * coloured run IS the distance travelled, and an agent that stopped reporting
 * has not un-travelled it. A BLOCK RECOLOURS ONLY THE STEP IT HAPPENED ON —
 * recolouring the run behind it would rewrite history the block never touched —
 * and it takes `--warning` with the octagon beside it, never the attention red
 * the agent axis owns (§7).
 *
 * `done` is the one state that claims the whole sequence, so it colours the
 * whole sequence: there is no current step left to single out.
 */
function stepTone(state: StepState, phase: PhaseView): string {
  if (phase.phase === "done") return "text-success";
  if (state === "ahead") return "text-content-tertiary";
  if (state === "current" && phase.blocked) return "text-warning";
  return "text-primary";
}

/**
 * The task axis: what the agent says it is doing about the job, as distinct
 * from what tmux and the session say about the process.
 *
 * The agent reports this by writing a file in its worktree — a containerized
 * agent can reach no API — so a workspace whose agent has reported nothing
 * carries no phase at all and this renders nothing rather than a fake step 0
 * (the caller renders the empty state instead).
 *
 * **THE TRACK IS HORIZONTAL AT EVERY WIDTH, and only its LABELS collapse.** It
 * used to fold into a six-row vertical list below `@3xl`, which is the width a
 * docked work panel almost never has — so the ordinary reading of this card was
 * a ladder taller than everything else on the tab, in a column where a sequence
 * read as a list of unrelated chips. Six dots and five connectors fit any width
 * this panel can be; six WORDS do not, so below `@lg/task` the words go and a
 * single sentence — the current phase and its step — carries the claim instead.
 * That sentence is the reason the collapse loses nothing: a reader still gets
 * the position in words, once, rather than six labels shrunk under 12px.
 *
 * A BACKWARD REPORT renders exactly like forward progress — see `stepState` —
 * and a skipped phase is not a lie; see `activeIndex` for what the wire
 * actually asserts. Both live in `adapters/phase-progress.ts`, with the rest of
 * what this track derives, so the arithmetic is testable without a DOM.
 *
 * Neither vendored `elements` stepper fits without editing it. `JobProgress` is
 * a single accumulating fill bar with no per-stage slot, and a bar that can only
 * grow reads a legitimate backward report as breakage. `Timeline`'s `time` field
 * is a plain `string`, which cannot host a live-updating relative age. So this
 * composes the same dot-and-connector language in Grove-owned markup.
 *
 * THREE CUES, THREE LIFETIMES, and `app/globals.css` owns all three because
 * `lint:styling` forbids a hue here and no Tailwind utility spells a keyframe.
 * The loop (`.phase-node-live`) says work is happening and stops when it stops;
 * the one-shots (`.phase-flash`, `.phase-note-flash`) say something just
 * changed and then get out of the way, so the static styling underneath has to
 * be correct on its own — a reader arriving after the fade has lost a
 * notification and nothing else; the connector split is static, because a
 * magnitude is not an event.
 */
export function PhaseMeter({
  phase,
  todo,
  working = false,
  enteredAt,
}: {
  phase: PhaseView | null;
  todo?: TodoProgressView | null;
  working?: boolean;
  enteredAt?: string | null;
}) {
  // Every hook runs before the empty case returns: a workspace that starts
  // reporting must not change this component's hook count mid-life.
  const now = useNow();
  const phaseFlash = useChangeFlash(phase?.phase ?? null);
  const noteFlash = useChangeFlash(phase?.note?.trim() || null);

  if (!phase) return null;
  const reached = activeIndex(phase);
  const note = phase.note?.trim() || null;
  // `null` all the way through: `inPhaseProgress` returns null for no checklist
  // and a null `fraction` for a checklist that licenses no claim, and both mean
  // the same thing to the connector — draw the plain rule.
  const fraction = inPhaseProgress(todo)?.fraction ?? null;
  const live = stepIsLive(phase, working);
  // `now === null` is the pre-mount render, which the server also produced —
  // and "not measured yet" is honestly not stale.
  const stale = now !== null && reportIsStale(phase, working, now);

  return (
    <>
      <CardRegion
        className="gap-2"
        data-testid="phase-meter"
        data-phase={phase.phase}
        data-blocked={phase.blocked || undefined}
      >
        <ol className="flex min-w-0 items-start" aria-label="Task phase">
          {PHASES.map((name, index) => {
            const state = stepState(index, reached);
            const PhaseIcon = phaseGlyph(name);
            const last = index === PHASES.length - 1;
            const tone = stepTone(state, phase);
            const current = state === "current";
            return (
              <li
                key={name}
                className={cn("flex min-w-0 items-start", !last && "flex-1")}
                aria-current={current ? "step" : undefined}
                data-done={state === "complete" || undefined}
                data-current={current || undefined}
              >
                {/* KEYED ON THE FLASH GENERATION, which is the whole mechanism:
                    a remount is what restarts a CSS animation, so a second
                    report of a new phase flashes again instead of sitting on a
                    finished one. Generation 0 is the first render, which must
                    not flash — arriving at a workspace is not a change. */}
                <span
                  key={current ? phaseFlash : undefined}
                  className={cn(
                    "relative flex min-w-0 flex-col items-center gap-1",
                    current && phaseFlash > 0 && "phase-flash",
                  )}
                >
                  {/* The halo is a sibling ring on this wrapper rather than a
                      shadow on the glyph, so it needs a positioned box the
                      glyph's own size. */}
                  <span
                    className={cn(
                      "relative flex size-4 shrink-0 items-center justify-center",
                      tone,
                      current && live && "phase-node-live",
                    )}
                  >
                    <PhaseIcon aria-hidden className="size-4 shrink-0" />
                  </span>
                  {/* Six abbreviated 10px labels fit a 300px pane; full 12px
                      words return only when this card has 420px to spend. */}
                  <span
                    className={cn(
                      "max-w-full truncate text-[10px] leading-3 @min-[420px]/task:hidden",
                      tone,
                      current && "font-bold",
                    )}
                  >
                    {PHASE_LABELS[name].short}
                  </span>
                  <span
                    className={cn(
                      "hidden max-w-full truncate text-[12px] leading-4 @min-[420px]/task:block",
                      tone,
                      current && "font-bold",
                    )}
                  >
                    {PHASE_LABELS[name].full}
                  </span>
                </span>
                {/* Aligned to the DOT rather than to the column, so a label of
                    any length leaves the connector on one straight line.

                    THE SEGMENT AFTER THE CURRENT NODE IS THE ONLY ONE THAT CAN
                    CARRY A FRACTION, because the checklist measures the phase
                    the agent is standing in and nothing else. With no checklist
                    there is no measurement, so this stays the plain rule rather
                    than a zero-width fill — an empty segment would claim the
                    phase has made no progress, which is a reading nobody
                    took (§11). */}
                {!last &&
                  (current && fraction !== null ? (
                    <span
                      aria-hidden
                      className="mt-2 flex h-px min-w-2 flex-1 overflow-hidden"
                      data-testid="phase-connector-split"
                    >
                      <span
                        className="phase-connector-fill h-px"
                        style={{ width: `${fraction * 100}%` }}
                      />
                      <span className="phase-connector-rest h-px flex-1" />
                    </span>
                  ) : (
                    <span
                      aria-hidden
                      className="mt-2 h-px min-w-2 flex-1 bg-border"
                    />
                  ))}
              </li>
            );
          })}
        </ol>
        {/* THE ONE ANNOUNCED THING. A phase change is a real event a reader
            wants told once; everything else here repaints on a clock tick, and
            a live region over any of it would narrate the minute hand. */}
        <span className="sr-only" aria-live="polite">
          {phaseLabel(phase.phase)}, step {phase.index + 1} of {phase.total}
          {phase.blocked ? ", blocked" : ""}
        </span>
      </CardRegion>

      {/* THE REPORT IS PROSE AND IT IS COMPLETE. A bounded region is an
          enclosure, not a crop: the note wraps to whatever it needs and no
          height is reserved for it, so a blank note removes this region and
          leaves the phase exactly where it was.

          BLOCKED IS A FLAG ON THE PHASE REACHED, never a seventh step and never
          a completion state — so it colours this region rather than moving the
          dot, and the word and the octagon's shape carry it with the hue only
          agreeing (§4.7). The reason and the block travel together because
          "stuck" without "why" is the report a reader has to go and ask about. */}
      {note && (
        <CardRegion
          key={noteFlash}
          className={cn(
            "relative",
            noteFlash > 0 && "phase-note-flash",
            phase.blocked && "border-destructive/40 bg-destructive/5",
          )}
          data-testid="phase-note"
        >
          {phase.blocked && (
            <Badge
              variant="destructive"
              className="w-fit"
              data-testid="phase-blocked"
              title={`The agent reports it cannot make progress in ${phase.phase}.`}
            >
              <OctagonAlertIcon aria-hidden />
              blocked
            </Badge>
          )}
          <p className="min-w-0 break-words text-sm text-content-secondary">
            {note}
          </p>
        </CardRegion>
      )}
      {/* A block with nothing said about it is still worth stating — with no
          region to carry the word, it rides the phase track's own row. */}
      {!note && phase.blocked && (
        <Badge
          variant="destructive"
          className="w-fit"
          data-testid="phase-blocked"
        >
          <OctagonAlertIcon aria-hidden />
          blocked
        </Badge>
      )}

      {/* WHEN THE CLAIM WAS MADE IS PART OF THE CLAIM. A phase with no age
          beside it reads as current however old it is, which is the one thing
          a track cannot show by shape — so the age rides under the report
          rather than inside it, and survives a workspace that reported a phase
          and no note at all. */}
      <div
        className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-xs text-content-tertiary"
        data-testid="phase-reported"
      >
        <span className={stale ? "text-warning" : undefined}>
          reported <RelativeTime iso={phase.updated_at} />
          {/* The WORD, not the hue, is what says this is too quiet — a
              coloured age is a state carried by colour alone (§4.7). */}
          {stale ? " · stale" : ""}
        </span>
        {enteredAt && <PhaseElapsed iso={enteredAt} />}
      </div>
    </>
  );
}

/**
 * How long the agent has held the phase it is reporting — a DURATION, which is
 * why it cannot be `RelativeTime`: "2h ago" places the entry in the past, and
 * the claim here is that it has been going on for two hours and still is.
 *
 * Same hydration contract as every other clock in `relative-time.tsx`: the
 * server paints the absolute instant and the duration replaces it after mount,
 * off the one shared `useNow` tick rather than a second interval of its own.
 */
function PhaseElapsed({ iso }: { iso: string }): React.ReactNode {
  const now = useNow();
  const exact = absoluteTime(iso);

  return (
    <time
      dateTime={iso}
      title={`Entered this phase at ${exact}`}
      data-testid="phase-elapsed"
    >
      {now === null ? exact : `${durationSince(iso, now)} in this phase`}
    </time>
  );
}

/**
 * A generation that increments whenever `value` changes after the first render.
 *
 * WHY A GENERATION AND NOT A TIMER. The cue is a CSS animation that ends at its
 * own resting frame, so nothing has to switch it off — and a `setTimeout` that
 * clears a class is a timer to cancel on unmount, a second source of truth
 * about whether the cue is running, and a repaint the animation did not need.
 * The caller keys the flashing element on this instead: a remount is what
 * restarts an animation, which is also the only way a second change re-fires
 * one that already finished.
 *
 * Zero means "nothing has changed yet", which is the first render — arriving at
 * a workspace mid-phase is not an event, so the caller renders no cue at 0.
 *
 * **NULL IS NOT A VALUE, AND THAT IS THE WHOLE BUG THIS GUARD EXISTS FOR.** The
 * phase arrives from a query, so the first render is `null` and the second is
 * the phase the agent has been reporting for an hour — and `null → "verifying"`
 * is indistinguishable from a real transition to a comparison that only asks
 * "did this differ". Measured on the built page: every load of a working
 * workspace flashed its current node, announcing a change that had not
 * happened. So a null ADOPTS silently, and only a value-to-different-value step
 * is an event. Same family as the "first value seen" trap in webapp/CLAUDE.md:
 * an unresolved query reads exactly like a fact.
 */
function useChangeFlash(value: string | null): number {
  const previous = useRef(value);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (previous.current === value) return;
    const wasUnknown = previous.current === null;
    previous.current = value;
    // Arriving at a value from "not answered yet", or losing one, is the query
    // resolving rather than the agent reporting.
    if (wasUnknown || value === null) return;
    setGeneration((count) => count + 1);
  }, [value]);

  return generation;
}

/**
 * The agent's checklist as a bounded meter — a MAGNITUDE, which is what makes a
 * plain accumulating bar right here and wrong for the phase track above it
 * (§6). It is the transcript Plan surface's summary, never a second copy of the
 * items: this card says how much is left, and the place that lists what is left
 * already exists.
 *
 * No counts means NO MODULE. `0 / 0` would be a fabricated measurement of a
 * checklist the agent never opened, which §11 rules out for exactly the reason
 * an empty bar is indistinguishable from a finished one.
 */
export function ChecklistMeter({ todo }: { todo?: TodoProgressView | null }) {
  if (!todo || todo.total <= 0) return null;
  const percent = Math.round((todo.completed / todo.total) * 100);

  return (
    <CardRegion data-testid="checklist-meter">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-content-secondary">
          Checklist
        </span>
        <span
          className="shrink-0 text-xs font-medium tabular-nums text-content-primary"
          data-testid="checklist-count"
        >
          {todo.completed} / {todo.total} done
        </span>
      </div>
      {/* The vendored `Progress` never forwards `value` to its Radix root, so
          the caller supplies `aria-valuenow` — see webapp/CLAUDE.md. */}
      <Progress
        value={percent}
        aria-valuenow={percent}
        aria-label="Checklist completion"
      />
      <span className="text-xs tabular-nums text-content-tertiary">
        {todo.in_progress} in progress · {todo.pending} pending
      </span>
    </CardRegion>
  );
}

/**
 * How far the workspace has got across every ticket it holds, as one bar — and
 * the label is the whole point of the component.
 *
 * **IT IS AVERAGE PHASE PROGRESS, NOT COMPLETION, AND THE TWO ARE BOTH TRUE.**
 * Two tickets at `delivering` (index 4 of six) average 80% while `0 / 2 done` is
 * equally correct: one measures position along the work, the other counts work
 * finished. Labelling this bar "done" — or replacing 80% with 0% to agree with
 * the count — discards every claim the agents actually made, which is the
 * larger dishonesty. The count sits directly under the bar so neither number
 * can be read without the other.
 *
 * A PLAIN BAR HERE, and a checkpoint track in `PhaseMeter`, is deliberate (§6).
 * A track answers "which step is this on", which only means something for a
 * single claim; asked of six tickets at once it has no answer. An aggregate can
 * only be read as more-or-less, which is the one case the accumulating fill is
 * right for.
 *
 * The coverage line is not decoration: `ticketRollup` scores an unclaimed ref as
 * zero, so without it a workspace nobody has reported on shows a confident 0%
 * that reads as "no progress" when the honest claim is "nobody has said".
 */
export function TicketProgressMeter({
  rollup,
}: {
  rollup: TicketRollup | null;
}) {
  if (!rollup) return null;
  const percent = Math.round(rollup.fraction * 100);

  return (
    <CardRegion data-testid="ticket-rollup">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 text-xs text-content-secondary">
          Average ticket phase progress
        </span>
        {/* §3's provenance rule: dashed on the VALUE, with the `title` carrying
            the arithmetic. Dashed, never dotted — dotted is `Explain`'s
            glossary affordance and the two appear on the same card. */}
        <span
          className="shrink-0 text-sm font-medium tabular-nums text-content-primary underline decoration-dashed underline-offset-2"
          title={rollupFormula(rollup)}
          data-testid="rollup-percent"
        >
          {percent}%
        </span>
      </div>
      <Progress
        value={percent}
        aria-valuenow={percent}
        aria-label="Average ticket phase progress across every attached ticket"
      />
      <span
        className="text-xs text-content-tertiary"
        data-testid="rollup-coverage"
      >
        {rollupCoverage(rollup)}
      </span>
    </CardRegion>
  );
}

/**
 * The header owns the current claim so the track can spend every width on
 * checkpoints without repeating itself.
 */
export function phaseSummary(phase: PhaseView | null): string | null {
  return phase
    ? `${phaseLabel(phase.phase)} · step ${phase.index + 1} of ${phase.total}`
    : null;
}
