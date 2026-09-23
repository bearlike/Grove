import { describe, expect, it } from "vitest";

import { rollupCoverage, rollupFormula, ticketRollup } from "@/components/grove/workspace/selectors";
import type { PhaseView, TicketRef } from "@/lib/grove/api";

/**
 * The aggregate's whole value is that it cannot flatter the workspace.
 *
 * Every test here pins a way the number could overstate progress, because that
 * is the only direction that misleads: a reader acts on "nearly done" and does
 * not act on "barely started". Understating costs a second look; overstating
 * costs a shipped assumption.
 */

const ref = (id: string): TicketRef =>
  ({ provider: "gitea", id, kind: "issue", ambiguous: false }) as TicketRef;

const claim = (
  ticket: string,
  phase: string,
  index: number,
  blocked = false,
): PhaseView["tickets"][number] =>
  ({ ticket, phase, index, blocked, note: null }) as unknown as PhaseView["tickets"][number];

const view = (tickets: PhaseView["tickets"]): PhaseView =>
  ({
    phase: "build",
    note: null,
    blocked: false,
    updated_at: "2026-08-11T00:00:00Z",
    index: 2,
    total: 6,
    tickets,
  }) as unknown as PhaseView;

describe("ticketRollup", () => {
  it("returns null with no tickets — a workspace is not a batch at 0%", () => {
    // The alternative would paint a permanently-empty bar on every
    // single-purpose workspace in the fleet, which is noise, not information.
    expect(ticketRollup([], view([]))).toBeNull();
  });

  it("scores an UNCLAIMED ticket as zero rather than excluding it", () => {
    // The overstatement this exists to prevent: five untouched tickets and one
    // finished one must not read 100%.
    const refs = [ref("1"), ref("2"), ref("3"), ref("4"), ref("5"), ref("6")];
    const r = ticketRollup(refs, view([claim("gitea:1", "handoff", 5)]));
    expect(r?.fraction).toBeCloseTo(1 / 6);
    expect(r?.done).toBe(1);
    expect(r?.unreported).toBe(5);
  });

  it("reports unreported separately so silence is distinguishable from no progress", () => {
    // A bare 0% cannot tell "nobody has said" from "nothing has happened", and
    // the reader acts differently on each.
    const r = ticketRollup([ref("1"), ref("2")], view([]));
    expect(r).toEqual({
      total: 2,
      reported: 0,
      unreported: 2,
      done: 0,
      blocked: 0,
      phases: {
        scope: 0,
        plan: 0,
        build: 0,
        verify: 0,
        deliver: 0,
        handoff: 0,
      },
      fraction: 0,
    });
  });

  it("puts handoff at exactly 1 and scope at exactly 0 — the ramp's real endpoints", () => {
    // `index / total` instead of `index / (total - 1)` would make a finished
    // ticket read 83%, which is the off-by-one a reader would report as a bug.
    expect(ticketRollup([ref("1")], view([claim("gitea:1", "handoff", 5)]))?.fraction).toBe(1);
    expect(ticketRollup([ref("1")], view([claim("gitea:1", "scope", 0)]))?.fraction).toBe(0);
  });

  it("keeps a blocked ticket's earned progress instead of zeroing it", () => {
    // Blocked is orthogonal to the phase — that is the axis's premise. A ticket
    // blocked at `verify` really has had four phases of work done on it.
    const r = ticketRollup([ref("1")], view([claim("gitea:1", "verify", 3, true)]));
    expect(r?.fraction).toBeCloseTo(0.6);
    expect(r?.blocked).toBe(1);
    expect(r?.done).toBe(0);
  });

  it("counts a ticket at handoff and blocked as handed off", () => {
    // The agent finished it and flagged something alongside. Hiding the
    // completion would be the stranger claim of the two.
    const r = ticketRollup([ref("1")], view([claim("gitea:1", "handoff", 5, true)]));
    expect(r?.done).toBe(1);
    expect(r?.blocked).toBe(1);
    expect(r?.fraction).toBe(1);
  });

  it("averages a genuinely mixed batch", () => {
    const refs = [ref("1"), ref("2"), ref("3"), ref("4")];
    const r = ticketRollup(
      refs,
      view([
        claim("gitea:1", "handoff", 5),
        claim("gitea:2", "verify", 3),
        claim("gitea:3", "scope", 0),
        claim("gitea:4", "plan", 1, true),
      ]),
    );
    // (1 + 0.6 + 0 + 0.2) / 4
    expect(r?.fraction).toBeCloseTo(0.45);
    expect(r).toMatchObject({
      total: 4,
      reported: 4,
      unreported: 0,
      done: 1,
      blocked: 1,
      phases: {
        scope: 1,
        plan: 1,
        build: 0,
        verify: 1,
        deliver: 0,
        handoff: 1,
      },
    });
  });

  it("ignores a claim naming a ticket this workspace does not hold", () => {
    // The join is by key, and a detached ticket's entry is deliberately left
    // standing in the phase file. It must not inflate a batch it left.
    const r = ticketRollup([ref("1")], view([claim("gitea:1", "scope", 0), claim("gitea:99", "handoff", 5)]));
    expect(r?.total).toBe(1);
    expect(r?.reported).toBe(1);
    expect(r?.fraction).toBe(0);
  });

  it("is null when the workspace has never reported, but tickets exist", () => {
    // No phase at all still yields an honest all-unreported rollup rather than
    // nothing — the tickets are real work whether or not anyone described it.
    const r = ticketRollup([ref("1"), ref("2")], null);
    expect(r).toMatchObject({ total: 2, reported: 0, unreported: 2, fraction: 0 });
  });
});

describe("the rollup's explanation", () => {
  it("keeps phase progress separate from completion", () => {
    // Delivering is index 4 of six, so 80% phase progress and no completed
    // tickets are both true. Collapsing either into the other loses the fact
    // this aggregate exists to make scannable.
    const rollup = ticketRollup(
      [ref("1"), ref("2")],
      view([claim("gitea:1", "deliver", 4), claim("gitea:2", "deliver", 4)]),
    );

    expect(rollup?.fraction).toBeCloseTo(0.8);
    expect(rollup && rollupCoverage(rollup)).toBe("0 / 2 done · 2 claims reported");
  });

  it("names a missing claim without excluding its ref from the average", () => {
    const rollup = ticketRollup([ref("1"), ref("2")], view([claim("gitea:1", "deliver", 4)]));

    expect(rollup?.fraction).toBeCloseTo(0.4);
    expect(rollup && rollupCoverage(rollup)).toBe("0 / 2 done · 1 claim not reported");
  });

  it("writes singular and plural coverage honestly, including the blocked suffix", () => {
    const oneReported = ticketRollup([ref("1")], view([claim("gitea:1", "handoff", 5)]));
    const blocked = ticketRollup(
      [ref("1"), ref("2")],
      view([claim("gitea:1", "verify", 3, true), claim("gitea:2", "scope", 0, true)]),
    );

    expect(oneReported && rollupCoverage(oneReported)).toBe("1 / 1 done · 1 claim reported");
    expect(blocked && rollupCoverage(blocked)).toBe("0 / 2 done · 2 claims reported · 2 blocked");
  });

  it("makes the calculation and its unreported-zero rule available on hover", () => {
    const rollup = ticketRollup([ref("1"), ref("2")], view([claim("gitea:1", "deliver", 4)]));

    expect(rollup && rollupFormula(rollup)).toContain("index / (total − 1)");
    expect(rollup && rollupFormula(rollup)).toContain("a ref with no claim counts as 0");
  });
});
