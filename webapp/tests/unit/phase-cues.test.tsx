import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseMeter } from "@/components/grove/workspace/phase-meter";
import type { TodoProgressView } from "@/lib/grove/api";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * The cues the Task card spends to say "look here", pinned at the only level a
 * static render can see: which element carries which class, and — the half that
 * actually regresses — WHICH STATES DECLINE TO SPEND ONE.
 *
 * The animations themselves are verified in a browser, because a still frame of
 * a pulsing node and a still frame of a dead one are the same image. What lives
 * here is the gating: a mark that moves when nothing is happening is a lie the
 * design system names explicitly, and it is a lie no screenshot catches either.
 */
const todo = (over: Partial<TodoProgressView> = {}): TodoProgressView => ({
  total: 0,
  completed: 0,
  in_progress: 0,
  pending: 0,
  ...over,
});

describe("the live pulse", () => {
  it("runs on the current step while the agent is working", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={FIXTURE_PHASE} working />,
    );

    expect(html).toContain("phase-node-live");
    // One node, not six: the cue names WHERE the work is, so a track that lit
    // every step would say nothing at all.
    expect(html.match(/phase-node-live/g)).toHaveLength(1);
  });

  it("does not run when the agent is idle", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={FIXTURE_PHASE} working={false} />,
    );

    expect(html).not.toContain("phase-node-live");
  });

  /**
   * The fixture can ONLY fail if the blocked arm of the gate exists: `working`
   * is true, so an implementation that checked liveness alone reaches the same
   * "should it pulse" question and answers yes.
   */
  it("does not run on a blocked phase even while the agent works", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={{ ...FIXTURE_PHASE, blocked: true }} working />,
    );

    expect(html).not.toContain("phase-node-live");
  });

  it("does not run once the work is done", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter
        phase={{ ...FIXTURE_PHASE, phase: "done", index: 5 }}
        working
      />,
    );

    expect(html).not.toContain("phase-node-live");
  });
});

describe("the in-phase connector", () => {
  /**
   * A REPORTED checklist is the only thing that licenses a fraction, and the
   * two failure directions are different: a missing split is a lost fact, a
   * present-but-empty one is a fabricated measurement. Both are asserted.
   */
  it("splits the segment after the current step when a checklist was reported", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter
        phase={FIXTURE_PHASE}
        todo={todo({ total: 4, completed: 1, in_progress: 1, pending: 2 })}
      />,
    );

    expect(html).toContain('data-testid="phase-connector-split"');
    expect(html).toContain("phase-connector-fill");
    expect(html).toContain("phase-connector-rest");
    // The width IS the claim — a split that rendered at a fixed width would
    // pass every structural assertion above while reporting the wrong number.
    expect(html).toContain("width:25%");
  });

  it("draws the plain rule when no checklist was reported", () => {
    const html = renderToStaticMarkup(<PhaseMeter phase={FIXTURE_PHASE} />);

    expect(html).not.toContain('data-testid="phase-connector-split"');
  });

  /**
   * An EMPTY checklist is not a checklist at zero percent. This is the case the
   * `total <= 0` guard exists for, and without it the split renders at 0% —
   * which reads as "this phase has made no progress", a claim nobody made.
   */
  it("draws the plain rule for an empty checklist rather than a zero-width fill", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={FIXTURE_PHASE} todo={todo()} />,
    );

    expect(html).not.toContain('data-testid="phase-connector-split"');
  });

  /**
   * A genuinely-zero-of-five checklist is a real measurement and DOES render,
   * which is what makes the case above a guard rather than a coincidence.
   */
  it("renders a real zero-of-five as a split at 0%", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter
        phase={FIXTURE_PHASE}
        todo={todo({ total: 5, completed: 0, pending: 5 })}
      />,
    );

    expect(html).toContain('data-testid="phase-connector-split"');
    expect(html).toContain("width:0%");
  });
});

describe("when the claim was made", () => {
  it("states the report's age beside the track", () => {
    const html = renderToStaticMarkup(<PhaseMeter phase={FIXTURE_PHASE} />);

    expect(html).toContain('data-testid="phase-reported"');
    expect(html).toContain("reported");
  });

  it("states the time in phase only when the history supplies an entry", () => {
    const without = renderToStaticMarkup(<PhaseMeter phase={FIXTURE_PHASE} />);
    const withEntry = renderToStaticMarkup(
      <PhaseMeter phase={FIXTURE_PHASE} enteredAt="2026-08-10T06:00:00Z" />,
    );

    expect(without).not.toContain('data-testid="phase-elapsed"');
    expect(withEntry).toContain('data-testid="phase-elapsed"');
  });
});

describe("the track's hue", () => {
  /**
   * A block recolours the step it happened on and nothing behind it. Asserted
   * as a COUNT because the failure mode is a tone that leaks along the run —
   * which still renders a warning somewhere and would satisfy a `toContain`.
   */
  it("spends warning on the blocked step alone, never on the run behind it", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={{ ...FIXTURE_PHASE, blocked: true }} />,
    );

    expect(html.match(/text-warning/g)).toHaveLength(3);
    expect(html).toContain("text-primary");
  });

  it("settles the whole sequence to success on done", () => {
    const html = renderToStaticMarkup(
      <PhaseMeter phase={{ ...FIXTURE_PHASE, phase: "done", index: 5 }} />,
    );

    expect(html).toContain("text-success");
    expect(html).not.toContain("text-primary");
  });
});
