import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseBadge } from "@/components/grove/fleet/badges";
import { phaseGlyph } from "@/components/grove/fleet/tokens";
import { TooltipProvider } from "@/components/ui/tooltip";
import { agentStatusProps } from "@/lib/grove/adapters";
import type { AgentActivityView, PhaseView } from "@/lib/grove/api";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * `blocked` is a FLAG ACROSS the phase axis, not a seventh phase, and every
 * assertion here is a way that could be got wrong.
 *
 * The position must survive (a blocked agent still got somewhere), the mark must
 * be findable without reading (a wall of twenty cards is scanned, not read), and
 * it must survive the colour being gone — `--destructive` and `--success` are
 * 12/255 apart in greyscale, so a hue that is the only carrier carries nothing.
 */

/**
 * `PhaseBadge` mounts a Radix `Tooltip` without self-providing (unlike
 * `Explain`, which does) — the app supplies one `TooltipProvider` at the root
 * (`providers.tsx`), and every other test exercising a bare tooltip wraps the
 * same way (`compaction-boundary.test.tsx`, `usage-quota.test.tsx`).
 *
 * Radix mounts `TooltipContent` only while OPEN and portals it, so none of the
 * hover copy exists in this markup at all. That is deliberate and it is why the
 * copy is pinned through `phaseTooltip` and `PhaseTooltipBody` below rather
 * than through the badge — the badge's contract here is the mark and the
 * `aria-label`, which is all a static render can honestly see.
 */
function badge(phase: Parameters<typeof PhaseBadge>[0]["phase"]): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <PhaseBadge phase={phase} />
    </TooltipProvider>,
  );
}

const BLOCKED: PhaseView = { ...FIXTURE_PHASE, phase: "implementing", index: 2, blocked: true };
const MOVING: PhaseView = { ...FIXTURE_PHASE, phase: "implementing", index: 2, blocked: false };

describe("phaseGlyph", () => {
  it("keeps the fill ramp for a phase that is moving", () => {
    expect(phaseGlyph("scoping")).toBe("○");
    expect(phaseGlyph("implementing")).toBe("◑");
    expect(phaseGlyph("done")).toBe("✓");
  });

  it("takes the glyph slot when the agent is stuck, for every phase alike", () => {
    // Blocked is orthogonal, so the mark cannot depend on WHERE it is stuck.
    const marks = new Set(
      (["scoping", "planning", "implementing", "verifying", "delivering", "done"] as const).map(
        (phase) => phaseGlyph(phase, true),
      ),
    );

    expect(marks.size).toBe(1);
    expect([...marks][0]).not.toBe(phaseGlyph("implementing"));
  });

  it("stays a distinct shape from every ramp step, so greyscale loses nothing", () => {
    const ramp = (["scoping", "planning", "implementing", "verifying", "delivering", "done"] as const)
      .map((phase) => phaseGlyph(phase));

    expect(ramp).not.toContain(phaseGlyph("scoping", true));
  });

  it("defaults to the ramp, so a caller that knows nothing of blocked still reads", () => {
    // `fleet-tree.tsx` calls this with one argument. The default is what keeps
    // that call site correct rather than accidentally blocked.
    expect(phaseGlyph("verifying")).toBe(phaseGlyph("verifying", false));
  });
});

describe("PhaseBadge", () => {
  it("renders nothing when the agent has never reported a phase", () => {
    expect(badge(null)).toBe("");
    expect(badge(undefined)).toBe("");
  });

  it("still reports the position reached when blocked", () => {
    // The whole point of a flag rather than a step: how far the agent got and
    // whether it is moving are two facts, and blocking must not erase the first.
    expect(badge(BLOCKED)).toContain("3/6");
    expect(badge(MOVING)).toContain("3/6");
  });

  it("spends `destructive` on blocked and stays quiet otherwise", () => {
    expect(badge(BLOCKED)).toContain('data-variant="destructive"');
    expect(badge(MOVING)).toContain('data-variant="outline"');
  });

  it("never claims the loudest variant, blocked or not", () => {
    // §6 allows one `default` per object across all its axes, and the workspace
    // status badge owns it. This axis has never claimed it and must not start.
    expect(badge(BLOCKED)).not.toContain('data-variant="default"');
    expect(badge(MOVING)).not.toContain('data-variant="default"');
  });

  it("says blocked in words for anything that is not looking at the colour", () => {
    const html = badge({ ...BLOCKED, note: null });

    expect(html).toContain('data-blocked="true"');
    // A screen reader and a hover both get the word, and both still get the
    // phase it is stuck in — `blocked` alone would lose the position the badge
    // exists to report.
    expect(html).toMatch(/aria-label="[^"]*blocked in implementing[^"]*step 3 of 6"/);
  });

  it("keeps the agent's own note behind the mark, so the WHY is one hover away", () => {
    expect(badge({ ...BLOCKED, note: "waiting on review" })).toContain(
      "task phase: blocked in implementing — waiting on review, step 3 of 6",
    );
  });

  it("carries NO native title, so the hover is the tooltip and only the tooltip", () => {
    // §3: one figure, one hover affordance. A `title` left beside a Radix
    // tooltip races it, and the browser's version cannot hold four claims.
    expect(badge(BLOCKED)).not.toContain("title=");
    expect(badge(MOVING)).not.toContain("title=");
  });

  it("is reachable by keyboard, because `Badge` is a bare span", () => {
    // Without this the explanation is mouse-only — which excludes exactly the
    // readers least able to decode `◐ 3/6` on sight.
    expect(badge(MOVING)).toContain('tabindex="0"');
  });

  it("carries no blocked marker when the work is moving", () => {
    const html = badge(MOVING);

    expect(html).not.toContain("data-blocked");
    expect(html).not.toContain("blocked");
  });

  it("keeps the phase name on the element either way, for a test or a probe", () => {
    expect(badge(BLOCKED)).toContain('data-phase="implementing"');
    expect(badge(MOVING)).toContain('data-phase="implementing"');
  });
});

/**
 * The workspace header pill. It reads the phase for its words, so a blocked task
 * that still printed "Implementing" would be the surface most likely to mislead
 * — it is the one line a reader checks before deciding to leave the agent alone.
 */
describe("agentStatusProps", () => {
  function activity(overrides: Partial<AgentActivityView> = {}): AgentActivityView {
    return {
      state: "working",
      title: null,
      current_task: null,
      human_turns: 3,
      assistant_replies: 9,
      replies_per_turn: [3, 3, 3],
      tool_calls: 12,
      active_subagents: 0,
      model: null,
      tokens_in: 0,
      tokens_out: 0,
      last_event_at: null,
      needs_attention: false,
      error_detail: null,
      questions: [],
      ...overrides,
    };
  }

  const NOW = new Date("2026-08-11T00:00:00Z");

  it("leads with the word, and keeps what the agent actually said", () => {
    const props = agentStatusProps(activity(), { ...BLOCKED, note: "waiting on review" }, NOW);

    expect(props.label).toBe("Blocked — waiting on review");
  });

  it("falls back to the phase word when the agent left no note", () => {
    const props = agentStatusProps(activity(), { ...BLOCKED, note: null }, NOW);

    expect(props.label).toBe("Blocked — Implementing");
  });

  it("leaves the label alone when nothing is blocked", () => {
    expect(agentStatusProps(activity(), { ...MOVING, note: null }, NOW).label).toBe("Implementing");
  });

  it("does not touch the activity mark, which answers a different question", () => {
    // An agent can be genuinely generating while the TASK it reports is blocked
    // on somebody else. Overwriting the mark would claim the process stalled.
    expect(agentStatusProps(activity({ state: "working" }), BLOCKED, NOW).state).toBe("working");
  });
});
