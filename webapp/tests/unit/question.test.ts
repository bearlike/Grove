import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  acceptsCustomText,
  buildAnswerPlan,
  needsExplicitSubmit,
  textError,
  toggleOption,
} from "@/components/grove/workspace/answer-plan";
import { PendingQuestion } from "@/components/grove/workspace/pending-question";
import { questionGroups, questionPresentation } from "@/lib/grove/adapters";
import type { AgentQuestionView } from "@/lib/grove/api";
import type { PendingQuestionGroup } from "@/lib/grove/runtime";
import { PLAN_CONFIRM, QUESTION_BATCH } from "../fixtures/turns";

/** The batch's single-select question, on its own — a lone tap submits it. */
const SINGLE_SELECT = QUESTION_BATCH[0];

/** The same question as a multiSelect, which the daemon refuses free text on. */
const MULTI_SELECT: AgentQuestionView = {
  ...SINGLE_SELECT,
  kind: "multi_select",
  multiselect: true,
};

function groupOf(questions: AgentQuestionView[]): PendingQuestionGroup {
  const presentation = questionPresentation(questions);
  if (!presentation) throw new Error("unreachable");
  return { groupId: presentation.groupId, questions, presentation };
}

function render(questions: AgentQuestionView[]): string {
  return renderToStaticMarkup(
    createElement(PendingQuestion, {
      group: groupOf(questions),
      onSubmit: () => {},
      submitting: false,
    }),
  );
}

describe("questionPresentation", () => {
  it("renders a lone confirm as a plan approval, plan text verbatim", () => {
    const presentation = questionPresentation(PLAN_CONFIRM);
    expect(presentation).toMatchObject({
      kind: "approval",
      state: "request",
      command: PLAN_CONFIRM[0].prompt,
      subtitle: "ExitPlanMode",
    });
  });

  it("renders an AskUserQuestion batch as ONE form, because it answers atomically", () => {
    const presentation = questionPresentation(QUESTION_BATCH);
    expect(presentation?.kind).toBe("elicitation");
    if (presentation?.kind !== "elicitation") throw new Error("unreachable");
    expect(presentation.fields).toHaveLength(2);
    // No shared prompt is invented for a batch — each question labels its field.
    expect(presentation.message).toBe("");
  });

  it("keeps the wire id as the field name — it is the answer-back address", () => {
    const presentation = questionPresentation(QUESTION_BATCH);
    if (presentation?.kind !== "elicitation") throw new Error("unreachable");
    expect(presentation.fields.map((f) => f.name)).toEqual(["toolu_batch#0", "toolu_batch#1"]);
  });

  it("types a field by whether the wire offered options", () => {
    const presentation = questionPresentation(QUESTION_BATCH);
    if (presentation?.kind !== "elicitation") throw new Error("unreachable");
    expect(presentation.fields[0]).toMatchObject({ kind: "choice", options: ["Primary only", "All replicas"] });
    expect(presentation.fields[1]).toMatchObject({ kind: "text" });
  });

  it("shows an unanswered question empty rather than fabricating a default", () => {
    const presentation = questionPresentation(QUESTION_BATCH);
    if (presentation?.kind !== "elicitation") throw new Error("unreachable");
    expect(presentation.fields.map((f) => f.value)).toEqual(["", ""]);
  });

  it("settles only when EVERY question in the batch is answered", () => {
    const half = [{ ...QUESTION_BATCH[0], answered: true }, QUESTION_BATCH[1]];
    expect(questionPresentation(half)).toMatchObject({ state: "request" });
    const all = QUESTION_BATCH.map((q) => ({ ...q, answered: true }));
    expect(questionPresentation(all)).toMatchObject({ state: "accepted" });
  });

  it("treats a confirm batched with real questions as a form, not an approval", () => {
    const mixed = [...PLAN_CONFIRM, { ...QUESTION_BATCH[0], group_id: "toolu_plan" }];
    expect(questionPresentation(mixed)?.kind).toBe("elicitation");
  });

  it("returns null for an empty group instead of an empty card", () => {
    expect(questionPresentation([])).toBeNull();
  });
});

describe("questionGroups", () => {
  it("splits by group_id and preserves wire order", () => {
    const groups = questionGroups([QUESTION_BATCH[0], PLAN_CONFIRM[0], QUESTION_BATCH[1]]);
    expect(groups.map((g) => g.map((q) => q.id))).toEqual([
      ["toolu_batch#0", "toolu_batch#1"],
      ["toolu_plan#0"],
    ]);
  });
});

describe("PendingQuestion", () => {
  it("wears the elicitation form anatomy while keeping live controls", () => {
    const presentation = questionPresentation(QUESTION_BATCH);
    if (!presentation) throw new Error("unreachable");
    const group: PendingQuestionGroup = {
      groupId: presentation.groupId,
      questions: QUESTION_BATCH,
      presentation,
    };

    const html = renderToStaticMarkup(
      createElement(PendingQuestion, { group, onSubmit: () => {}, submitting: false }),
    );

    expect(html).toContain('data-slot="elicitation-form"');
    expect(html).toContain('data-testid="pending-question"');
    expect(html.match(/data-testid="question-option"/g)).toHaveLength(2);
    expect(html).toContain('data-testid="question-text"');
    expect(html).toContain('data-testid="question-submit"');
  });

  it("keeps a confirm batch read-only", () => {
    const presentation = questionPresentation(PLAN_CONFIRM);
    if (!presentation) throw new Error("unreachable");
    const group: PendingQuestionGroup = {
      groupId: presentation.groupId,
      questions: PLAN_CONFIRM,
      presentation,
    };

    const html = renderToStaticMarkup(
      createElement(PendingQuestion, { group, onSubmit: () => {}, submitting: false }),
    );

    expect(html).toContain('data-testid="question-approval"');
    expect(html).not.toContain('data-testid="pending-question"');
    expect(html).not.toContain('data-testid="question-submit"');
  });
});

describe("answer plan", () => {
  it("keeps a multi-question batch atomic until every answer exists", () => {
    expect(needsExplicitSubmit(QUESTION_BATCH)).toBe(true);
    expect(buildAnswerPlan(QUESTION_BATCH, { "toolu_batch#0": { kind: "indexes", indexes: [1] } })).toBeNull();
    expect(
      buildAnswerPlan(QUESTION_BATCH, {
        "toolu_batch#0": { kind: "indexes", indexes: [1] },
        "toolu_batch#1": { kind: "text", text: " /health " },
      }),
    ).toEqual([{ selected_indexes: [1] }, { text: "/health" }]);
  });

  it("refuses to build a plan from text the daemon would reject", () => {
    // A pasted wrapped line is the reachable case; it must never reach the POST.
    expect(
      buildAnswerPlan([QUESTION_BATCH[1]], {
        "toolu_batch#1": { kind: "text", text: "/health\n/ready" },
      }),
    ).toBeNull();
  });

  it("toggles multiselect choices without dropping the other selections", () => {
    const first = toggleOption(undefined, 2, true);
    const second = toggleOption(first, 0, true);
    const third = toggleOption(second, 2, true);

    expect(first).toEqual({ kind: "indexes", indexes: [2] });
    expect(second).toEqual({ kind: "indexes", indexes: [2, 0] });
    expect(third).toEqual({ kind: "indexes", indexes: [0] });
  });
});

describe("textError", () => {
  it("passes ordinary text, and an empty box is incomplete rather than invalid", () => {
    expect(textError("/health")).toBeNull();
    expect(textError("")).toBeNull();
    expect(textError("   ")).toBeNull();
  });

  it("rejects every control byte the daemon rejects", () => {
    // Named by CODE POINT, never as a literal or an escape: a control character
    // in this source would be invisible, which is the bug this rule exists for.
    // NUL, tab, LF, CR, ESC, DEL — the ends and the interesting middle of
    // `ord < 0x20 or ord == 0x7f`.
    for (const code of [0x00, 0x09, 0x0a, 0x0d, 0x1b, 0x1f, 0x7f]) {
      expect(textError(`a${String.fromCharCode(code)}b`)).not.toBeNull();
    }
  });
});

describe("custom answers — the option the agent did not offer", () => {
  it("is offered on single-select only, the one kind the daemon accepts text on", () => {
    expect(acceptsCustomText(SINGLE_SELECT)).toBe(true);
    expect(acceptsCustomText(MULTI_SELECT)).toBe(false);
    expect(acceptsCustomText(QUESTION_BATCH[1])).toBe(false);
    expect(acceptsCustomText(PLAN_CONFIRM[0])).toBe(false);
  });

  it("renders the box beside a single-select's options", () => {
    expect(render([SINGLE_SELECT])).toContain('data-testid="question-custom"');
  });

  it("withholds it from a multiSelect, whose text answer the daemon 422s", () => {
    expect(render([MULTI_SELECT])).not.toContain('data-testid="question-custom"');
  });
});

describe("option descriptions", () => {
  it("prints each description — it is what distinguishes two options", () => {
    const html = render([SINGLE_SELECT]);
    expect(html).toContain("Report only the primary connection.");
    expect(html).toContain("Fan out to every configured replica.");
    expect(html.match(/data-testid="question-option-description"/g)).toHaveLength(2);
  });

  it("keeps upstream's wrapping pill row when no option carries one", () => {
    const bare: AgentQuestionView = {
      ...SINGLE_SELECT,
      options: [
        { label: "Yes", description: null },
        { label: "No", description: null },
      ],
    };
    const html = render([bare]);
    expect(html).not.toContain('data-testid="question-option-description"');
    expect(html).toContain("rounded-full");
  });
});
