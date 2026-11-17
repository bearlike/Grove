import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  OVERVIEW_COLLISION_PADDING,
  overviewAlign,
} from "@/components/grove/fleet/overview-placement";

/**
 * The session overview grows from the card's edge toward the viewport's
 * vertical centre, and never sits against a viewport edge.
 *
 * Radix positions a tooltip relative to its trigger and can only be told which
 * EDGE of the trigger to line up with. So "move toward the centre" is decided
 * here from where the card sits, and Radix keeps the panel beside the card.
 */
describe("overviewAlign", () => {
  const H = 1000;

  it("grows downward from a card in the upper part of the viewport", () => {
    // A card near the top: anchoring its top edge sends the panel toward the centre.
    expect(overviewAlign(80, H)).toBe("start");
    expect(overviewAlign(300, H)).toBe("start");
  });

  it("grows upward from a card in the lower part of the viewport", () => {
    expect(overviewAlign(920, H)).toBe("end");
    expect(overviewAlign(700, H)).toBe("end");
  });

  it("centres on a card that already sits near the middle", () => {
    expect(overviewAlign(500, H)).toBe("center");
    expect(overviewAlign(470, H)).toBe("center");
    expect(overviewAlign(530, H)).toBe("center");
  });

  it("treats an unmeasured viewport as centred rather than throwing", () => {
    expect(overviewAlign(100, 0)).toBe("center");
    expect(overviewAlign(Number.NaN, H)).toBe("center");
  });
});

describe("the row tooltip wiring", () => {
  const tree = readFileSync(
    new URL("../../components/grove/fleet/fleet-tree.tsx", import.meta.url),
    "utf8",
  );
  const tooltip = tree.slice(
    tree.indexOf('className="session-overview-tooltip"') - 400,
    tree.indexOf('className="session-overview-tooltip"') + 60,
  );

  it("keeps a margin from every viewport edge", () => {
    // A positive pad is what stops the panel sitting flush against an edge.
    expect(OVERVIEW_COLLISION_PADDING).toBeGreaterThanOrEqual(16);
    expect(tooltip).toContain("collisionPadding={OVERVIEW_COLLISION_PADDING}");
  });

  it("measures the card on the move Radix opens on, not only on entry", () => {
    // A pointer already resting on a card after a navigation never re-enters
    // it, so an entry-only measure opened the panel against a stale default.
    expect(tree).toContain("onPointerMove={placeOverview}");
    expect(tree).not.toContain("onPointerEnter={placeOverview}");
    expect(tree).toContain("onFocus={placeOverview}");
  });

  it("stays horizontally beside the card and lets placement choose the edge", () => {
    expect(tooltip).toContain('side="right"');
    expect(tooltip).toContain("align={align}");
  });
});
