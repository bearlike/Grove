import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseMeter, TicketRollupMeter } from "@/components/grove/workspace/phase-meter";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * The stepper is a wrapping row of chips rather than a vertical checklist, so
 * what has to hold is the marking — which chips are behind the agent, which one
 * it is on — independently of how many fit on a line.
 */
function render(phase: Parameters<typeof PhaseMeter>[0]["phase"]): string {
  return renderToStaticMarkup(<PhaseMeter phase={phase} />);
}

function renderRollup(rollup: Parameters<typeof TicketRollupMeter>[0]["rollup"]): string {
  return renderToStaticMarkup(<TicketRollupMeter rollup={rollup} />);
}

const ROLLUP = {
  total: 4,
  reported: 3,
  unreported: 1,
  done: 1,
  blocked: 1,
  phases: {
    scoping: 0,
    planning: 1,
    implementing: 0,
    verifying: 1,
    delivering: 0,
    done: 1,
  },
  fraction: 0.45,
} as const;

/** Each step's own markup, so a claim about which chip OWNS a connector can be
 * made per chip rather than over the whole document — where a leading and a
 * trailing layout are indistinguishable. */
function steps(html: string): string[] {
  return html.split("<li").slice(1);
}

describe("PhaseMeter", () => {
  it("renders nothing at all when the agent has reported no phase", () => {
    // Not a fake step 0: a containerized agent that never wrote its phase file
    // has said nothing, and inventing "scoping" would be a claim Grove cannot
    // make.
    expect(render(null)).toBe("");
  });

  it("reports every phase count and keeps blocked outside the phase ramp", () => {
    const html = renderRollup(ROLLUP);

    expect(html).toContain('data-testid="ticket-phase-counts"');
    for (const [phase, count] of Object.entries(ROLLUP.phases)) {
      expect(html).toContain(`${phase} ${count}`);
    }
    expect(html).toContain('data-testid="rollup-blocked"');
    expect(html).toContain("1 blocked");
    expect(html).toContain('data-testid="rollup-unreported"');
  });

  it("does not invent a blocked ticket where the count is zero", () => {
    expect(renderRollup({ ...ROLLUP, blocked: 0 })).not.toContain('data-testid="rollup-blocked"');
  });

  it("marks every phase before the reported one done, and that one current", () => {
    const html = render(FIXTURE_PHASE);

    expect(html.match(/data-done="true"/g)).toHaveLength(4);
    expect(html.match(/data-current="true"/g)).toHaveLength(1);
    expect(html).toMatch(/data-current="true"[^>]*aria-current="step"|aria-current="step"/);
    expect(html).toContain("delivering");
    expect(html).toContain(FIXTURE_PHASE.note!);
  });

  it("completes all six when the agent reports done", () => {
    const html = render({ ...FIXTURE_PHASE, phase: "done", index: 5, note: null });

    expect(html.match(/data-done="true"/g)).toHaveLength(6);
    expect(html).not.toContain('data-current="true"');
  });

  it("lays the six phases out as one wrapping row", () => {
    expect(render(FIXTURE_PHASE)).toContain("flex-wrap");
  });

  it("tones each chip from the ui/badge taxonomy, never colour alone", () => {
    const html = render(FIXTURE_PHASE);

    // default = the one live step, secondary = settled/behind, outline = not
    // reached — three tones AND three glyphs, so the state survives greyscale.
    expect(html.match(/data-variant="secondary"/g)).toHaveLength(4);
    expect(html.match(/data-variant="default"/g)).toHaveLength(1);
    expect(html.match(/data-variant="outline"/g)).toHaveLength(1);
  });

  it("surfaces the note beside a live-updating, hover-exact timestamp", () => {
    const html = render(FIXTURE_PHASE);

    expect(html).toContain(FIXTURE_PHASE.note!);
    expect(html).toContain('data-testid="relative-time"');
    expect(html).toContain(`dateTime="${FIXTURE_PHASE.updated_at}"`);
  });

  it("renders no note row when the agent left none", () => {
    const html = render({ ...FIXTURE_PHASE, note: null });
    expect(html).not.toContain('data-testid="relative-time"');
  });

  it("reads a backward report the same as any other position, not as an error", () => {
    // verifying (index 3) walking back to planning (index 1): what used to
    // read complete simply reads ahead again, with no error styling anywhere.
    const forward = render({ ...FIXTURE_PHASE, phase: "verifying", index: 3, note: null });
    const backward = render({ ...FIXTURE_PHASE, phase: "planning", index: 1, note: null });

    expect(forward.match(/data-done="true"/g)).toHaveLength(3);
    expect(backward.match(/data-done="true"/g)).toHaveLength(1);
    // No chip ever takes the destructive tone — a regression is a correct
    // report, not a fault, so nothing here is styled as an error state.
    expect(backward).not.toContain('data-variant="destructive"');
  });

  it("marks blocked as a FLAG beside the track, never as a seventh step", () => {
    const html = render({ ...FIXTURE_PHASE, blocked: true });

    expect(html).toContain('data-testid="phase-blocked"');
    expect(html).toContain('data-blocked="true"');
    // The track is untouched: the reached position is still reported, because
    // how far the agent got and whether it is moving are two different facts.
    expect(html.match(/data-done="true"/g)).toHaveLength(4);
    expect(html.match(/data-current="true"/g)).toHaveLength(1);
    // Six chips, not seven — the mark sits outside the ordered list, so nothing
    // reads as a checkpoint the agent is meant to reach.
    expect(html.match(/<li /g)).toHaveLength(6);
  });

  it("leads with the WORD, so the mark survives greyscale", () => {
    // `--destructive` and `--success` measure 12/255 apart in greyscale, so a
    // hue that is the only carrier carries nothing.
    expect(render({ ...FIXTURE_PHASE, blocked: true })).toContain("blocked");
  });

  it("spends exactly one loud mark, and it is not a second `default`", () => {
    const html = render({ ...FIXTURE_PHASE, blocked: true });

    expect(html.match(/data-variant="destructive"/g)).toHaveLength(1);
    expect(html.match(/data-variant="default"/g)).toHaveLength(1);
  });

  it("draws no blocked mark when the agent is moving", () => {
    const html = render({ ...FIXTURE_PHASE, blocked: false });

    expect(html).not.toContain('data-testid="phase-blocked"');
    expect(html).not.toContain("data-blocked");
    expect(html).not.toContain('data-variant="destructive"');
  });

  it("completes the track from the wire's own count, not its own label list", () => {
    // `total` exists so a client never pins a second copy of the vocabulary. A
    // meter that counted its labels would be exactly that copy.
    const html = render({ ...FIXTURE_PHASE, phase: "done", index: 5, total: 6, note: null });

    expect(html.match(/data-done="true"/g)).toHaveLength(6);
  });

  it("marks every phase before a skipped-to position complete, not just the reported one", () => {
    // An agent may jump straight to implementing (index 2) without ever
    // reporting scoping or planning — Grove only ever holds the latest claim,
    // so "before the reported index" is the only signal there is.
    const html = render({ ...FIXTURE_PHASE, phase: "implementing", index: 2, note: null });
    expect(html.match(/data-done="true"/g)).toHaveLength(2);
    expect(html.match(/data-current="true"/g)).toHaveLength(1);
  });

  /**
   * The wrap behaviour, which is a real regression rather than a nicety: chips
   * were joined by a LEADING connector, so a chip that wrapped took its
   * connector with it — leaving a stub dangling at the start of the new row and
   * nothing joining it to the row above.
   */
  it("trails each connector behind its own chip, so a wrapped row never opens with a dangling rule", () => {
    // The discriminator between the two layouts, and the reason it is asserted
    // per-`li` rather than on the document: BOTH draw five rules for six chips,
    // so counting them cannot tell them apart. Which chip OWNS a rule can.
    // Leading: the first chip has none and the last carries one. Trailing: the
    // first carries one and the last has none — which is what stops a wrapped
    // row starting with a rule attached to nothing above it.
    const items = steps(render(FIXTURE_PHASE));

    expect(items).toHaveLength(6);
    expect(items[0]).toContain("bg-border");
    expect(items.at(-1)).not.toContain("bg-border");
  });

  it("gives the last phase no trailing connector, so the sequence has a definite end", () => {
    const html = render({ ...FIXTURE_PHASE, phase: "done", index: 5, note: null });

    // Six chips, five joins — never a rule running off the end of `done`.
    expect(html.match(/bg-border/g)).toHaveLength(5);
    expect(steps(html).at(-1)).not.toContain("bg-border");
  });

  it("lets the connector absorb the slack, so each row spans its width instead of packing left", () => {
    // A fixed-width rule leaves every row ragged, which is what reads as
    // "wrapped under protest" rather than as a track.
    expect(render(FIXTURE_PHASE)).toContain("flex-1");
  });

  it("spaces wrapped rows apart vertically without opening a gap the connector cannot cross", () => {
    // Scoped to the track's own class list: the note paragraph below it
    // legitimately uses `gap-x`, and asserting over the whole document would
    // fail on a line that has nothing to do with the chips.
    const track = render(FIXTURE_PHASE).match(/<ol class="([^"]*)"/)![1];

    expect(track).toContain("gap-y-2");
    // A `gap-x` here would disconnect every chip from its neighbour to solve a
    // vertical problem; horizontal spacing stays the connector's job.
    expect(track).not.toMatch(/\bgap-x-/);
  });
});
