import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildSteps, DEMO_PROMPTS, DIAGRAM_TAB_SELECTOR, ISSUE_OPS_PROMPT } from "@/components/grove/onboarding/steps";
import { DEMO_TURNS } from "@/components/grove/onboarding/demo-workspace";
import { code } from "./_source";

/**
 * EVERY TOUR ANCHOR MUST EXIST IN SOURCE. A step whose selector matches nothing
 * renders nothing at all now (`disableWhenSelectorFalsy`) — no throw, no type
 * error, and the tour looks stuck at exactly that step. The anchors are
 * `data-testid`/`data-pill`/`data-slot` attributes owned by OTHER files, so a
 * rename there is what this census catches. Both variants of the diagram step
 * are examined, since only one is ever built for a given fleet.
 */
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : path.endsWith(".tsx") ? [path] : [];
  });
}

const SOURCE = [...walk("components/grove"), ...walk("components/elements")]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

/** Literal ids, plus the template ones the rail nav, the pills and the tab strip interpolate. */
const ANCHORS = new Set<string>([
  ...Array.from(SOURCE.matchAll(/data-testid="([a-z-]+)"/g), (match) => match[1]!),
  ...Array.from(SOURCE.matchAll(/data-testid=\{LAUNCH_TESTIDS\.(\w+)\}/g), (match) => `launch-${match[1]!}`),
  ...Array.from(SOURCE.matchAll(/data-slot="([a-z-]+)"/g), (match) => `slot-${match[1]!}`),
  ...Array.from(SOURCE.matchAll(/kind="([a-z]+)"/g), (match) => `pill-${match[1]!}`),
  ...["terminal", "info", "changes", "files", "controls", "diagram"].map((tab) => `work-panel-tab-${tab}`),
]);

const LAUNCH_VALUES = new Set(
  Array.from(
    readFileSync("components/grove/launch/launch-state.tsx", "utf8").matchAll(/^\s+\w+: "(launch-[a-z-]+)",$/gm),
    (match) => match[1]!,
  ),
);

/** Every attribute a selector names, as census keys. */
function keysOf(selector: string): string[] {
  return [
    ...Array.from(selector.matchAll(/data-testid="([^"]+)"/g), (m) => m[1]!),
    ...Array.from(selector.matchAll(/data-pill="([^"]+)"/g), (m) => `pill-${m[1]!}`),
    ...Array.from(selector.matchAll(/data-slot="([^"]+)"/g), (m) => `slot-${m[1]!}`),
  ];
}

const WITHOUT_DIAGRAM = buildSteps({ workspaceId: "w", hasDiagram: false });
const WITH_DIAGRAM = buildSteps({ workspaceId: "w", hasDiagram: true });
const EMPTY_FLEET = buildSteps({ workspaceId: null, hasDiagram: false });
const ALL = [...WITHOUT_DIAGRAM, ...WITH_DIAGRAM];

describe("onboarding steps", () => {
  it("examined the tree", () => {
    expect(ANCHORS.size).toBeGreaterThan(50);
    expect(WORK_PANEL_SOURCE).toContain("work-panel-tab-${value}");
  });

  it.each(ALL.map((step) => [step.selector, step.title] as const))(
    "%s anchors a real element (%s)",
    (selector) => {
      const keys = keysOf(selector);
      expect(keys.length, selector).toBeGreaterThan(0);
      for (const key of keys) {
        expect(ANCHORS.has(key) || LAUNCH_VALUES.has(key), `${selector}: ${key} matches nothing`).toBe(true);
      }
    },
  );

  it("the tour walks the demo workspace, never a real one", () => {
    const tour = readFileSync("components/grove/onboarding/onboarding-tour.tsx", "utf8");
    expect(tour).toContain("workspaceId: DEMO_WORKSPACE_ID, hasDiagram: true");
    expect(tour).not.toContain("sortedFleetRows");
  });

  it("writes the demo into the activity cache on open rather than invalidating it", () => {
    // The daemon streams a full snapshot once, on connect; an invalidate of
    // a query whose interval is off is a no-op, so the demo would only appear
    // after some unrelated reconnect. The write is what makes step 13 render.
    const tour = code("components/grove/onboarding/onboarding-tour.tsx");
    expect(tour).toMatch(/setQueryData<DashboardSnapshotView>\(groveKeys\.activity,[\s\S]*withDemoWorkspace/);
    expect(tour).not.toMatch(/invalidateQueries\(\{ queryKey: groveKeys\.activity \}\)/);
  });

  it("the landing brief and the sample transcript tell one story", () => {
    expect(DEMO_TURNS.turns[0]!.user_text).toContain("issue #412");
    expect(DEMO_TURNS.turns[0]!.user_text).toContain("Grove diagram tools");
    expect(ISSUE_OPS_PROMPT).toContain("issue #412");
    expect(ISSUE_OPS_PROMPT).toContain("Grove diagram tools");
  });

  it("is eighteen steps with a workspace, and the diagram step is one of them either way", () => {
    expect(WITHOUT_DIAGRAM).toHaveLength(18);
    expect(WITH_DIAGRAM).toHaveLength(18);
    expect(WITH_DIAGRAM.some((step) => step.selector === DIAGRAM_TAB_SELECTOR)).toBe(true);
    expect(WITHOUT_DIAGRAM.some((step) => step.demand?.kind === "workspace-prompt")).toBe(true);
  });

  it("drops the workspace walk, not the closing step, when the fleet is empty", () => {
    expect(EMPTY_FLEET.every((step) => step.route !== "workspace")).toBe(true);
    expect(EMPTY_FLEET.at(-1)?.route).toBe("any");
  });

  it("reads standing up: one-line titles, bodies under 300 characters", () => {
    for (const step of ALL) {
      expect(step.title.length, step.title).toBeLessThan(40);
      expect(step.body.length, step.title).toBeLessThan(300);
    }
  });

  it("every landing brief the tour writes is one the reset can recognise and clear", () => {
    const written = ALL.flatMap((step) => (step.demand?.kind === "prompt" ? [step.demand.text] : []));
    expect(written.length).toBeGreaterThan(0);
    for (const text of written) expect(DEMO_PROMPTS).toContain(text);
  });

  it("the account menu and the landing page both offer the tour", () => {
    expect(readFileSync("components/grove/account/index.tsx", "utf8")).toContain("account-take-tour");
    expect(readFileSync("components/grove/launch/launch-surface.tsx", "utf8")).toContain("launch-take-tour");
  });
});

const WORK_PANEL_SOURCE = readFileSync("components/grove/workspace/work-panel.tsx", "utf8");
