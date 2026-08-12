import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseTooltipBody, type PhaseMark } from "@/components/grove/fleet/badges";
import { phaseTooltip, type PhaseTooltipTicket } from "@/components/grove/workspace/selectors";
import type { TaskPhase } from "@/components/grove/fleet/types";

/**
 * WHAT `◐ 3/6` MEANS, FOR SOMEONE WHO WAS NEVER TAUGHT THE VOCABULARY.
 *
 * The mark is a position in a six-step order the reader has never seen, beside a
 * ticket whose tracker is making a completely separate claim about the same
 * object. So the two failures this pins are: saying nothing a newcomer can use,
 * and saying it in a way that merges the two axes into one apparent status.
 *
 * The copy is pinned through the pure composer rather than through the rendered
 * badge because Radix mounts `TooltipContent` only while open and portals it —
 * a static render of `PhaseBadge` contains none of these sentences.
 */

const PHASES: readonly TaskPhase[] = [
  "scoping",
  "planning",
  "implementing",
  "verifying",
  "delivering",
  "done",
];

function mark(overrides: Partial<PhaseMark> = {}): PhaseMark {
  return { phase: "implementing", note: null, index: 2, total: 6, blocked: false, ...overrides };
}

function ticket(overrides: Partial<PhaseTooltipTicket> = {}): PhaseTooltipTicket {
  return { provider: "gitea", kind: "issue", status: "open", ...overrides };
}

describe("what the phase word means", () => {
  it("explains every phase in the vocabulary, with no gaps", () => {
    // A `Record` over the wire union, so a seventh phase is a compile error
    // rather than a badge that silently explains nothing.
    for (const phase of PHASES) {
      expect(phaseTooltip(mark({ phase })).meaning.length).toBeGreaterThan(10);
    }
  });

  it("says something DIFFERENT for each one, or the sentence is decoration", () => {
    const meanings = new Set(PHASES.map((phase) => phaseTooltip(mark({ phase })).meaning));

    expect(meanings.size).toBe(PHASES.length);
  });

  it("keeps the substance of the six definitions the agent itself is given", () => {
    // `skills/working-in-grove/SKILL.md` is the source. These are the load-bearing
    // nouns of each row; losing one means the sentence was rewritten rather than
    // turned around into third person.
    const meaning = (phase: TaskPhase) => phaseTooltip(mark({ phase })).meaning;

    expect(meaning("scoping")).toContain("reading the ticket");
    expect(meaning("planning")).toContain("choosing an approach");
    expect(meaning("implementing")).toContain("editing files");
    expect(meaning("verifying")).toContain("tests, linters or the build");
    expect(meaning("delivering")).toContain("pull request");
    expect(meaning("done")).toContain("Handed off");
  });

  it("makes the agent the subject, because the reader is WATCHING rather than doing", () => {
    // The skill writes these TO the agent ("You are editing files"); a human
    // reading their own dashboard is not the one editing files.
    for (const phase of PHASES) {
      expect(phaseTooltip(mark({ phase })).meaning).not.toMatch(/\bYou\b|\byour\b/);
    }
  });

  it("promises nothing about recency, because no per-ticket timestamp exists", () => {
    const tip = phaseTooltip(mark({ note: "rebasing onto main" }), ticket());
    const copy = [tip.headline, tip.meaning, tip.stall, tip.tracker].join(" ");

    expect(copy).not.toMatch(/\bstill\b|\bsince\b|\bas of\b|\bago\b|\bjust\b/i);
  });
});

describe("the headline", () => {
  it("leads with the state word the badge is already showing", () => {
    expect(phaseTooltip(mark({ phase: "verifying" })).headline).toBe("verifying");
  });

  it("says blocked AND where it stopped — a bare `blocked` drops the position", () => {
    expect(phaseTooltip(mark({ phase: "verifying", blocked: true })).headline).toBe(
      "blocked in verifying",
    );
  });

  it("stays a bare lowercase chip word with no period, like the badge it explains", () => {
    for (const blocked of [false, true]) {
      const { headline } = phaseTooltip(mark({ blocked }));

      expect(headline).toBe(headline.toLowerCase());
      expect(headline.endsWith(".")).toBe(false);
    }
  });
});

describe("blocked", () => {
  it("adds one sentence about the WORK, and names the object it is about", () => {
    expect(phaseTooltip(mark({ blocked: true }), ticket()).stall).toBe(
      "The agent cannot finish this issue, so it stays at this step until someone clears the block.",
    );
    expect(phaseTooltip(mark({ blocked: true }), ticket({ kind: "pull_request" })).stall).toContain(
      "this pull request",
    );
  });

  it("degrades to the task itself where there is no ticket", () => {
    expect(phaseTooltip(mark({ blocked: true })).stall).toContain("this task");
  });

  it("never claims the agent is waiting on you RIGHT NOW — that is a different axis", () => {
    // The activity axis's `blocked` means "stopped at a prompt" and clears when
    // you answer it. This one is a claim about the work that survives the agent
    // dying or being respawned, so the copy must not borrow the other's words.
    const { stall } = phaseTooltip(mark({ blocked: true }), ticket());

    expect(stall).not.toMatch(/waiting for you|permission|prompt|answer/i);
  });

  it("says nothing at all when the work is moving", () => {
    expect(phaseTooltip(mark({ blocked: false }), ticket()).stall).toBeNull();
  });

  it("still reports the position it reached, exactly as the badge does", () => {
    expect(phaseTooltip(mark({ blocked: true, index: 3 })).aria).toContain("step 4 of 6");
  });
});

describe("the agent's note", () => {
  it("survives VERBATIM — it is somebody else's sentence, not ours to rewrite", () => {
    const note = "gates fail on main; needs 498 merged first";

    expect(phaseTooltip(mark({ note })).note).toBe(note);
  });

  it("is absent rather than empty when the agent wrote nothing", () => {
    expect(phaseTooltip(mark({ note: null })).note).toBeNull();
    expect(phaseTooltip(mark({ note: "   " })).note).toBeNull();
  });
});

describe("the tracker's own claim, kept apart from Grove's", () => {
  it("attributes the state to the tracker BY NAME, so it cannot read as Grove's", () => {
    expect(phaseTooltip(mark(), ticket()).tracker).toBe("Gitea says this issue is open.");
    expect(phaseTooltip(mark(), ticket({ provider: "github", kind: "pull_request", status: "merged" })).tracker).toBe(
      "GitHub says this pull request is merged.",
    );
  });

  it("has a sentence for every state Grove normalizes to", () => {
    const say = (status: string) => phaseTooltip(mark(), ticket({ status })).tracker;

    expect(say("closed")).toContain("is closed.");
    expect(say("reopened")).toContain("is open.");
    expect(say("draft")).toContain("is a draft.");
  });

  it("quotes a word it could not normalize rather than inventing one", () => {
    // `unknown` is a real state: the tracker HAS said something, Grove just has
    // no controlled term for it. Marked, not interpreted.
    expect(phaseTooltip(mark(), ticket({ status: "in progress" })).tracker).toBe(
      "Gitea says this issue is “in progress”.",
    );
  });

  it("says nothing when the tracker has said nothing", () => {
    // A ref can be attached before its tracker was ever reachable, and the row
    // shows no status badge in that case either.
    expect(phaseTooltip(mark(), ticket({ status: null })).tracker).toBeNull();
  });

  it("is absent entirely on a FLEET phase, which belongs to a workspace", () => {
    const tip = phaseTooltip(mark());

    expect(tip.tracker).toBeNull();
    expect(tip.meaning.length).toBeGreaterThan(10);
  });
});

describe("a closed ticket the agent is only just scoping", () => {
  // The combination readers conflate: the tracker has finished with it and Grove
  // has barely started. Both are true, and the tooltip's whole job is to let a
  // reader hold both without deciding one of them is wrong.
  const tip = phaseTooltip(mark({ phase: "scoping", index: 0 }), ticket({ status: "closed" }));

  it("reports both claims, each with its claimant named", () => {
    expect(tip.headline).toBe("scoping");
    expect(tip.meaning).toBe(
      "The agent is reading the ticket, the code and the tests, working out what the job actually is.",
    );
    expect(tip.tracker).toBe("Gitea says this issue is closed.");
  });

  it("never joins them into one sentence, which is what makes them read as one axis", () => {
    expect(tip.meaning).not.toContain("closed");
    expect(tip.tracker).not.toContain("scoping");
  });

  it("carries both into the accessible name too, in the same order", () => {
    expect(tip.aria).toBe("task phase: scoping, step 1 of 6. Gitea says this issue is closed.");
  });
});

describe("the accessible name", () => {
  it("is at least what the old native title and aria-label carried between them", () => {
    expect(phaseTooltip(mark({ blocked: true, note: "waiting on review" })).aria).toBe(
      "task phase: blocked in implementing — waiting on review, step 3 of 6",
    );
  });
});

describe("house voice", () => {
  it("writes full sentences with an initial cap and a trailing period", () => {
    const tip = phaseTooltip(mark({ blocked: true, note: "n" }), ticket());

    for (const line of [tip.meaning, tip.stall, tip.tracker]) {
      expect(line).toMatch(/^[A-Z].*\.$/s);
    }
  });

  it("never opens a line with an instruction", () => {
    for (const phase of PHASES) {
      const tip = phaseTooltip(mark({ phase, blocked: true }), ticket());

      for (const line of [tip.meaning, tip.stall]) {
        expect(line).not.toMatch(/^(Check|Open|Run|Look|Click|See|Try|Use)\b/);
      }
    }
  });

  it("keeps every line to one sentence, or one sentence plus one clause", () => {
    for (const phase of PHASES) {
      const tip = phaseTooltip(mark({ phase, blocked: true }), ticket());

      for (const line of [tip.meaning, tip.stall!, tip.tracker!]) {
        expect(line.match(/\.\s/g) ?? []).toHaveLength(0);
      }
    }
  });
});

describe("the rendered body", () => {
  function body(tip: ReturnType<typeof phaseTooltip>): string {
    return renderToStaticMarkup(<PhaseTooltipBody tip={tip} />);
  }

  it("stacks every claim it was given", () => {
    const html = body(phaseTooltip(mark({ blocked: true, note: "needs 498 merged" }), ticket()));

    expect(html).toContain("blocked in implementing");
    expect(html).toContain("The agent is editing files.");
    expect(html).toContain("cannot finish this issue");
    expect(html).toContain("needs 498 merged");
    expect(html).toContain("Gitea says this issue is open.");
  });

  it("draws no empty rows for the claims that were not made", () => {
    const html = body(phaseTooltip(mark()));

    expect(html).not.toContain("phase-tooltip-stall");
    expect(html).not.toContain("phase-tooltip-note");
    expect(html).not.toContain("phase-tooltip-tracker");
  });

  it("marks the agent's note as a quotation, so it reads as its words not ours", () => {
    const html = body(phaseTooltip(mark({ note: "rebasing" })));

    expect(html).toMatch(/“rebasing”/);
  });

  it("carries no colour at all, because it sits on the INVERTED tooltip surface", () => {
    // `--content-*` is tuned against `--background`; on `TooltipContent`'s
    // `bg-foreground` it measures under AA (see `usage/window-meter.tsx`).
    // Rank comes from weight and order instead.
    const html = body(phaseTooltip(mark({ blocked: true, note: "x" }), ticket()));

    expect(html).not.toMatch(/text-content-|text-destructive|text-success|text-muted-foreground/);
  });
});
