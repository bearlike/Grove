import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseMeter } from "@/components/grove/workspace/phase-meter";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * The stepper is a wrapping row of chips rather than a vertical checklist, so
 * what has to hold is the marking — which chips are behind the agent, which one
 * it is on — independently of how many fit on a line.
 */
function render(phase: Parameters<typeof PhaseMeter>[0]["phase"]): string {
  return renderToStaticMarkup(<PhaseMeter phase={phase} />);
}

describe("PhaseMeter", () => {
  it("renders nothing at all when the agent has reported no phase", () => {
    // Not a fake step 0: a containerized agent that never wrote its phase file
    // has said nothing, and inventing "scoping" would be a claim Grove cannot
    // make.
    expect(render(null)).toBe("");
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
});
