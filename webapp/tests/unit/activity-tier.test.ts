import { describe, it, expect } from "vitest";
import { activityRank, isActive, tierForActivity } from "@/lib/grove/activity-tier";

describe("tierForActivity (agent state)", () => {
  it("working → active, full opacity, no highlight ring", () => {
    const t = tierForActivity("working", "active");
    expect(t.tier).toBe("active");
    expect(t.opacityClass).toBe("opacity-100");
    expect(t.treatment).toBe("none");
    expect(t.accentVar).toBe("var(--agent-working)");
  });

  it("waiting → attention, highlighted (ring), full opacity", () => {
    const t = tierForActivity("waiting", "active");
    expect(t.tier).toBe("attention");
    expect(t.opacityClass).toBe("opacity-100");
    expect(t.treatment).toBe("ring");
    expect(t.accentVar).toBe("var(--agent-waiting)");
  });

  it("blocked and error are attention too", () => {
    expect(tierForActivity("blocked", "active").tier).toBe("attention");
    expect(tierForActivity("error", "active").tier).toBe("attention");
    expect(tierForActivity("error", "active").accentVar).toBe("var(--agent-error)");
  });

  it("idle → dormant, dimmed (opacity-70)", () => {
    const t = tierForActivity("idle", "active");
    expect(t.tier).toBe("dormant");
    expect(t.opacityClass).toBe("opacity-70");
  });

  it("starting / unknown → dormant, dimmed hardest (opacity-55)", () => {
    expect(tierForActivity("starting", "active").opacityClass).toBe("opacity-55");
    expect(tierForActivity("unknown", "active").opacityClass).toBe("opacity-55");
  });
});

describe("tierForActivity (tmux/status fallback, no agent session)", () => {
  it("null + ACTIVE → active tier", () => {
    const t = tierForActivity(null, "active");
    expect(t.tier).toBe("active");
    expect(t.opacityClass).toBe("opacity-100");
  });

  it("null + RUNNING → active tier", () => {
    expect(tierForActivity(null, "running").tier).toBe("active");
  });

  it("null + IDLE → dormant, dimmed", () => {
    const t = tierForActivity(null, "idle");
    expect(t.tier).toBe("dormant");
    expect(t.opacityClass).toBe("opacity-70");
  });

  it("null + OFFLINE → dormant, dimmed hardest", () => {
    const t = tierForActivity(null, "offline");
    expect(t.tier).toBe("dormant");
    expect(t.opacityClass).toBe("opacity-55");
  });
});

describe("activityRank ordering (lower = first)", () => {
  it("active < attention < dormant", () => {
    expect(activityRank("working", "active")).toBe(0);
    expect(activityRank("waiting", "active")).toBe(1);
    expect(activityRank("idle", "active")).toBe(2);
    expect(activityRank("working", "active")).toBeLessThan(activityRank("waiting", "active"));
    expect(activityRank("waiting", "active")).toBeLessThan(activityRank("idle", "active"));
  });

  it("sorts a mixed set so running floats to the front", () => {
    const states: Array<["working" | "idle" | "waiting", number]> = [
      ["idle", 0],
      ["working", 0],
      ["waiting", 0],
    ];
    const sorted = [...states].sort(
      (a, b) => activityRank(a[0], "active") - activityRank(b[0], "active"),
    );
    expect(sorted.map((s) => s[0])).toEqual(["working", "waiting", "idle"]);
  });

  it("the tmux fallback ranks active workspaces ahead of idle ones", () => {
    expect(activityRank(null, "active")).toBeLessThan(activityRank(null, "idle"));
  });
});

describe("isActive", () => {
  it("is true only for the active tier", () => {
    expect(isActive("working", "idle")).toBe(true);
    expect(isActive(null, "active")).toBe(true);
    expect(isActive("waiting", "active")).toBe(false);
    expect(isActive("idle", "active")).toBe(false);
    expect(isActive(null, "offline")).toBe(false);
  });
});
