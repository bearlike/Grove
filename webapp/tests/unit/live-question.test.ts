import { describe, it, expect } from "vitest";
import { livePendingQuestions } from "@/lib/grove/live-question";
import { snapshot, workspace } from "@/tests/_helpers/activity-fixtures";
import type { AgentQuestionView } from "@/lib/grove/types";

const QUESTION: AgentQuestionView = {
  id: "g-1#0",
  group_id: "g-1",
  kind: "single_select",
  prompt: "Which migration strategy?",
  header: "Decision needed",
  options: [
    { label: "Big-bang cutover", description: "Faster, riskier" },
    { label: "Incremental", description: "Slower, safer" },
  ],
  multiselect: false,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
};

const TOPPINGS: AgentQuestionView = {
  id: "g-1#1",
  group_id: "g-1",
  kind: "multi_select",
  prompt: "Toppings?",
  header: null,
  options: [
    { label: "Cheese", description: null },
    { label: "Mushrooms", description: null },
  ],
  multiselect: true,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
};

describe("livePendingQuestions", () => {
  it("returns [] when there is no snapshot yet", () => {
    expect(livePendingQuestions(null, "a", "s-a")).toEqual([]);
  });

  it("returns [] when sessionId is null (no recorded session yet)", () => {
    const state = snapshot(workspace("a", "blocked", undefined, [QUESTION]));
    expect(livePendingQuestions(state, "a", null)).toEqual([]);
  });

  it("returns [] for a workspace/session the snapshot doesn't have", () => {
    const state = snapshot(workspace("a", "working"));
    expect(livePendingQuestions(state, "zzz", "s-a")).toEqual([]);
    expect(livePendingQuestions(state, "a", "s-unknown")).toEqual([]);
  });

  it("returns [] when the matched session has no pending questions", () => {
    const state = snapshot(workspace("a", "working"));
    expect(livePendingQuestions(state, "a", "s-a")).toEqual([]);
  });

  it("returns the question payload when the matched session is blocked on one", () => {
    const state = snapshot(workspace("a", "blocked", undefined, [QUESTION]));
    expect(livePendingQuestions(state, "a", "s-a")).toEqual([QUESTION]);
  });

  it("returns the WHOLE group, in order, for a genuine multi-question batch", () => {
    const state = snapshot(workspace("a", "blocked", undefined, [QUESTION, TOPPINGS]));
    expect(livePendingQuestions(state, "a", "s-a")).toEqual([QUESTION, TOPPINGS]);
  });

  it("finds the right workspace among several in the snapshot", () => {
    const state = snapshot(
      workspace("a", "working"),
      workspace("b", "blocked", undefined, [QUESTION]),
    );
    expect(livePendingQuestions(state, "b", "s-b")).toEqual([QUESTION]);
    expect(livePendingQuestions(state, "a", "s-a")).toEqual([]);
  });

  it("filters out an already-answered question as not pending (defensive)", () => {
    const answered = { ...QUESTION, answered: true, answer: "Incremental" };
    const state = snapshot(workspace("a", "waiting", undefined, [answered, TOPPINGS]));
    // TOPPINGS is still unanswered — only the resolved one drops.
    expect(livePendingQuestions(state, "a", "s-a")).toEqual([TOPPINGS]);
  });

  it("tolerates a malformed/garbage snapshot shape without throwing", () => {
    // The turns-view test's blanket fetch stub can hand the activity poll a
    // response shaped like something else entirely (see turns-view.test.tsx);
    // this must degrade to "no pending questions", never throw.
    const garbage = { turns: [] } as unknown as Parameters<typeof livePendingQuestions>[0];
    expect(() => livePendingQuestions(garbage, "a", "s-a")).not.toThrow();
    expect(livePendingQuestions(garbage, "a", "s-a")).toEqual([]);
  });
});
