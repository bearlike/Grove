import { describe, expect, it } from "vitest";

import { agentPlanProps, todoListProps } from "@/lib/grove/adapters";
import { NON_PREFIX_TODO } from "../fixtures/turns";

/*
 * `latestTodoFromTurns` and its four tests are GONE, not moved. It derived the
 * plan by scanning loaded turns, which the windowed transcript made unsound —
 * the newest board routinely falls outside the tail. `useWorkspaceTodo` reads
 * the daemon's `latest_todo` seam instead, so there is no client-side rule
 * left here to pin.
 */

describe("todoListProps", () => {
  it("prefers the present-tense active_form for the in-progress item only", () => {
    const { items } = todoListProps(NON_PREFIX_TODO);
    expect(items.map((i) => i.text)).toEqual([
      "Map the router module",
      "Add the endpoint",
      "Adding a test",
    ]);
  });

  it("maps wire statuses onto the vendored component's vocabulary", () => {
    expect(todoListProps(NON_PREFIX_TODO).items.map((i) => i.status)).toEqual([
      "pending",
      "done",
      "active",
    ]);
  });

  it("keys on position, because an agent may repeat a step verbatim", () => {
    const repeated = { items: [NON_PREFIX_TODO.items[0], NON_PREFIX_TODO.items[0]] };
    const ids = todoListProps(repeated).items.map((i) => i.id);
    expect(new Set(ids).size).toBe(2);
  });
});

describe("agentPlanProps", () => {
  it("reports activeIndex as a COUNT of finished steps, not the active position", () => {
    // The fixture's completed item is in the middle. Treating index-of-active as
    // progress would mark the earlier pending step done.
    expect(agentPlanProps(NON_PREFIX_TODO)).toEqual({
      steps: ["Map the router module", "Add the endpoint", "Add a test"],
      activeIndex: 1,
    });
  });
});
