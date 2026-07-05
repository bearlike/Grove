import { describe, it, expect } from "vitest";
import {
  answerItemFor,
  buildAnswerPlan,
  needsExplicitSubmit,
  type QuestionSelection,
} from "@/lib/grove/question-plan";
import type { AgentQuestionView } from "@/lib/grove/types";

function question(over: Partial<AgentQuestionView> = {}): AgentQuestionView {
  return {
    id: "q-1#0",
    group_id: "q-1",
    kind: "single_select",
    prompt: "Which strategy?",
    header: null,
    options: [
      { label: "Big-bang", description: null },
      { label: "Incremental", description: null },
    ],
    multiselect: false,
    answered: false,
    answer: null,
    source_tool: "AskUserQuestion",
    ...over,
  };
}

describe("needsExplicitSubmit", () => {
  it("is false for a lone single-select question", () => {
    expect(needsExplicitSubmit([question()])).toBe(false);
  });

  it("is true for any multiSelect member, even alone", () => {
    expect(needsExplicitSubmit([question({ kind: "multi_select", multiselect: true })])).toBe(
      true,
    );
  });

  it("is true whenever more than one question is in the group", () => {
    expect(needsExplicitSubmit([question(), question({ id: "q-1#1" })])).toBe(true);
  });
});

describe("answerItemFor", () => {
  it("returns null while nothing has been selected yet", () => {
    expect(answerItemFor(question(), undefined)).toBeNull();
  });

  it("builds {selected_indexes:[i]} for a single index pick", () => {
    const sel: QuestionSelection = { kind: "index", index: 1 };
    expect(answerItemFor(question(), sel)).toEqual({ selected_indexes: [1] });
  });

  it("builds a sorted {selected_indexes} for a multiSelect toggle set", () => {
    const q = question({ kind: "multi_select", multiselect: true });
    const sel: QuestionSelection = { kind: "indexes", indexes: [2, 0] };
    expect(answerItemFor(q, sel)).toEqual({ selected_indexes: [0, 2] });
  });

  it("returns null for an empty multiSelect toggle set (incomplete)", () => {
    const q = question({ kind: "multi_select", multiselect: true });
    const sel: QuestionSelection = { kind: "indexes", indexes: [] };
    expect(answerItemFor(q, sel)).toBeNull();
  });

  it("builds {text} for a free-text ('Other') answer, trimmed", () => {
    const sel: QuestionSelection = { kind: "text", text: "  Kiwi  " };
    expect(answerItemFor(question(), sel)).toEqual({ text: "Kiwi" });
  });

  it("returns null for blank free text (incomplete)", () => {
    const sel: QuestionSelection = { kind: "text", text: "   " };
    expect(answerItemFor(question(), sel)).toBeNull();
  });
});

describe("buildAnswerPlan", () => {
  it("builds one ordered item per question for a single-question group", () => {
    const q = question();
    const plan = buildAnswerPlan([q], { [q.id]: { kind: "index", index: 0 } });
    expect(plan).toEqual([{ selected_indexes: [0] }]);
  });

  it("returns null while any question in the group is still unanswered", () => {
    const a = question({ id: "g#0" });
    const b = question({ id: "g#1", prompt: "Toppings?" });
    const plan = buildAnswerPlan([a, b], { [a.id]: { kind: "index", index: 0 } });
    expect(plan).toBeNull();
  });

  it("builds a multi-question plan in group order once every question is answered", () => {
    const color = question({ id: "g#0", prompt: "Color?" });
    const toppings = question({
      id: "g#1",
      prompt: "Toppings?",
      kind: "multi_select",
      multiselect: true,
      options: [
        { label: "Cheese", description: null },
        { label: "Mushrooms", description: null },
      ],
    });
    const plan = buildAnswerPlan([color, toppings], {
      [color.id]: { kind: "index", index: 1 },
      [toppings.id]: { kind: "indexes", indexes: [0, 1] },
    });
    expect(plan).toEqual([{ selected_indexes: [1] }, { selected_indexes: [0, 1] }]);
  });

  it("builds a free-text plan for an optionless free_text-kind question", () => {
    const q = question({ kind: "free_text", options: [] });
    const plan = buildAnswerPlan([q], { [q.id]: { kind: "text", text: "Kiwi" } });
    expect(plan).toEqual([{ text: "Kiwi" }]);
  });
});
