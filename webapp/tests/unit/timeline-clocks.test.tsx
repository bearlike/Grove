import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DurationView, WorkspaceActivityView } from "@/lib/grove/api";
import { CardField, CardFields } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { sessionClocks } from "@/components/grove/workspace/selectors";

/**
 * The Info tab's Timeline rows: which session they read, and when they render.
 *
 * A session's two clocks diverge by design — ten sub-agents running ten minutes
 * side by side honestly read `10m` and `100m` — so the failure this guards
 * against is not a wrong number but a MISSING or MISATTRIBUTED one: a row that
 * silently describes a different session than the counts beside it, or a pair
 * of "not measured" rows on a workspace that has nothing to measure.
 */

function activity(sessions: readonly { duration?: DurationView | null }[]): WorkspaceActivityView {
  // Only the field under test is real; the rest of the row never reaches
  // `sessionClocks`, and inventing a whole plausible session would suggest it
  // does.
  return { sessions } as unknown as WorkspaceActivityView;
}

const MEASURED: DurationView = {
  active_ms: 600_000,
  execution_ms: 6_000_000,
  elapsed_span_ms: 900_000,
  confidence: "measured",
};

describe("sessionClocks", () => {
  it("reads the first session, the same row the activity counts come from", () => {
    const second: DurationView = { ...MEASURED, active_ms: 1 };

    expect(sessionClocks(activity([{ duration: MEASURED }, { duration: second }]))).toBe(MEASURED);
  });

  it("is null with no activity at all, so the rows never claim an unmeasured zero", () => {
    expect(sessionClocks(null)).toBeNull();
  });

  it("is null when a workspace has no session — there is nothing here to time", () => {
    expect(sessionClocks(activity([]))).toBeNull();
  });

  it("is null when the session reports no duration, rather than a pair of nulls", () => {
    // The distinction the both-or-neither rule exists for: two rows reading
    // "not measured" claim the clocks exist and were not taken.
    expect(sessionClocks(activity([{ duration: null }]))).toBeNull();
  });

  it("still yields a low-confidence reading — `unknown` means how well, not whether", () => {
    const derived: DurationView = { ...MEASURED, confidence: "unknown" };

    expect(sessionClocks(activity([{ duration: derived }]))).toBe(derived);
  });
});

describe("a clock row's label", () => {
  it("carries its own definition, because compute exceeding wall clock reads as a bug", () => {
    const html = renderToStaticMarkup(
      <CardFields>
        <CardField label={<Explain term="compute_time" />}>100m</CardField>
      </CardFields>,
    );

    // The label is a NODE, not a string — that widening is what lets Grove's
    // vocabulary be explained where it is used rather than only in prose.
    expect(html).toContain("Compute time");
    expect(html).toContain('data-testid="explain-compute_time"');
  });

  it("explains the two halves compute time splits into, which need it more", () => {
    // "Model wait" and "Tool time" have to say they ADD UP to the row above
    // them, or a reader takes them for two more independent clocks and asks
    // why none of the four agrees with any other.
    const html = renderToStaticMarkup(
      <CardFields>
        <CardField label={<Explain term="model_wait" />}>60m</CardField>
        <CardField label={<Explain term="tool_time" />}>40m</CardField>
      </CardFields>,
    );

    expect(html).toContain('data-testid="explain-model_wait"');
    expect(html).toContain('data-testid="explain-tool_time"');
    expect(html).toContain("Model wait");
    expect(html).toContain("Tool time");
  });
});

describe("the split the wire publishes", () => {
  it("partitions execution time and leaves the wall clock alone", () => {
    // The invariant the daemon guarantees, restated where the browser reads
    // it: two halves that sum to `execution_ms`, and NO union counterpart —
    // `active_ms` merges overlaps, so adding two merged halves would count a
    // tool that ran while a sub-agent generated twice.
    const clocks = sessionClocks(activity([{ duration: SPLIT }]));

    expect(clocks).not.toBeNull();
    expect(clocks!.generation_ms! + clocks!.tool_ms!).toBe(clocks!.execution_ms);
    expect(clocks!.active_ms).toBeLessThanOrEqual(clocks!.execution_ms!);
  });
});

const SPLIT: DurationView = {
  active_ms: 600_000,
  execution_ms: 6_000_000,
  generation_ms: 4_000_000,
  tool_ms: 2_000_000,
  elapsed_span_ms: 900_000,
  confidence: "derived",
};
