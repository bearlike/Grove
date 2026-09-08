import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  answerItem,
  buildAnswerPlan,
  confirmedGroupPlan,
  textError,
  toggleOption,
  typeText,
} from "@/components/grove/workspace/answer-plan";
import { PendingQuestion } from "@/components/grove/workspace/pending-question";
import { HistoricalQuestion } from "@/components/grove/workspace/question-view";
import { questionGroups, questionPresentation } from "@/lib/grove/adapters";
import type { AgentQuestionView } from "@/lib/grove/api";
import type { PendingQuestionGroup } from "@/lib/grove/runtime";
import { PLAN_CONFIRM, QUESTION_BATCH } from "../fixtures/turns";

/** The batch's single-select question, on its own — a lone tap submits it. */
const SINGLE_SELECT = QUESTION_BATCH[0];

/** The same question as a multiSelect, which the daemon now answers end to end. */
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
  it("leaves a plan approval alone — it has its own surface", () => {
    // `ApprovalCard` cannot show a Markdown document or per-option
    // consequences, so a plan must NOT be routed through this adapter.
    const presentation = questionPresentation(PLAN_CONFIRM);
    expect(presentation?.kind).not.toBe("approval");
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
  it("renders one shared collapsible card per live question", () => {
    const html = render(QUESTION_BATCH);

    expect(html).toContain('data-testid="pending-question"');
    expect(html.match(/data-testid="question-card"/g)).toHaveLength(2);
    expect(html.match(/data-testid="question-toggle"/g)).toHaveLength(2);
    // Every card is answerable now, the optionless free-text one included.
    expect(html.match(/data-testid="question-confirm"/g)).toHaveLength(2);
    expect(html).not.toContain('data-testid="question-submit"');
  });

  it("answers a plan by offering the agent's OWN rows, never a prose field", () => {
    const html = render(PLAN_CONFIRM);

    expect(html).toContain('data-testid="pending-question"');
    expect(html).toContain('data-question-kind="plan_approval"');
    expect(html).toContain('data-testid="plan-approval"');
    expect(html).toMatch(/<svg[^>]*class="[^"]*lucide-clipboard-check[^"]*"[^>]*aria-hidden="true"/);
    // One button per row the dialog paints, each stating its consequence.
    expect(html.match(/data-testid="plan-option"/g)).toHaveLength(3);
    expect(html.match(/data-testid="plan-option-description"/g)).toHaveLength(3);
    // The click IS the answer: no text field, no stance templates, no Confirm.
    expect(html).not.toContain('data-testid="question-text"');
    expect(html).not.toContain('data-testid="question-stance"');
    expect(html).not.toContain('data-testid="question-confirm"');
    expect(html).not.toContain("<textarea");
  });

  it("renders the plan as a bounded document rather than a card header", () => {
    const html = render(PLAN_CONFIRM);

    // The plan is a BODY. In the header it sat inside a <button>, where block
    // Markdown is invalid HTML and nothing bounded its height.
    expect(html).toContain('data-testid="plan-body"');
    expect(html).not.toContain('data-testid="question-toggle"');
  });

  it("offers every row the wire carried, in the wire's own order", () => {
    // Order is the contract: the answer is a POSITION, so a surface that
    // reordered or dropped a row would approve something the reader did not
    // pick. Labels are Grove's wording and may change; the sequence may not.
    const html = render(PLAN_CONFIRM);
    const positions = PLAN_CONFIRM[0].options.map((option) => html.indexOf(option.label));

    expect(positions.every((at) => at >= 0)).toBe(true);
    expect([...positions]).toEqual([...positions].sort((a, b) => a - b));
  });

  it("never offers a plan the prose controls, which the engine would reject", () => {
    // The defect this replaces: Approve wrote the sentence "Yes, go ahead with
    // this plan." into a field, and the engine delivered it as prose after an
    // Escape that this dialog records as a REJECTION. A row index cannot
    // degrade that way, and the absence of the field is what guarantees it.
    const html = render(PLAN_CONFIRM);

    expect(html).not.toContain("Yes, go ahead with this plan.");
    expect(html).not.toContain("No, do not go ahead with this plan.");
  });

  it("offers the stance buttons on confirm alone — every other kind has options or a bare field", () => {
    for (const question of [SINGLE_SELECT, MULTI_SELECT, QUESTION_BATCH[1]]) {
      expect(render([question])).not.toContain('data-testid="question-stance"');
    }
  });

  it("shares the live card anatomy with resolved history and reports its answer", () => {
    const resolved = { ...SINGLE_SELECT, answered: true, answer: "Primary only" };
    const live = render([SINGLE_SELECT]);
    const historical = renderToStaticMarkup(createElement(HistoricalQuestion, { question: resolved }));

    for (const html of [live, historical]) {
      expect(html).toContain('data-testid="question-card"');
      expect(html).toContain('data-testid="question-toggle"');
      expect(html).toContain(SINGLE_SELECT.prompt);
      expect(html).toContain("Report only the primary connection.");
      expect(html).toContain("Fan out to every configured replica.");
    }
    expect(historical).toContain('data-testid="question-answer"');
    expect(historical).toContain("Answer:");
    expect(historical).toContain("Primary only");
    expect(historical).not.toContain('data-testid="question-confirm"');
  });

  it("gives each question its own confirmation rather than a batch Send", () => {
    const questions = [SINGLE_SELECT, MULTI_SELECT];
    const html = render(questions);

    expect(html.match(/Confirm answer/g)).toHaveLength(2);
    expect(html).not.toContain(">Send<");
  });

  it("renders every ASKED question kind in one card anatomy, all answerable", () => {
    // A plan is deliberately absent: it is a mode choice, not an ask, so it
    // gets its own surface. Everything a human answers in words shares this one.
    const kinds: AgentQuestionView[] = [SINGLE_SELECT, MULTI_SELECT, QUESTION_BATCH[1]];
    const html = render(kinds);

    expect(html.match(/data-testid="question-card"/g)).toHaveLength(3);
    expect(html.match(/data-testid="question-confirm"/g)).toHaveLength(3);
    for (const kind of ["single_select", "multi_select", "free_text"]) {
      expect(html).toContain(`data-question-kind="${kind}"`);
    }
  });
});

describe("answerItem", () => {
  it("emits BOTH keys when a choice and a sentence were given", () => {
    expect(answerItem({ indexes: [1], text: "  and skip the replicas  " })).toEqual({
      selected_indexes: [1],
      text: "and skip the replicas",
    });
  });

  it("omits the key that says nothing rather than sending an empty one", () => {
    expect(answerItem({ indexes: [2, 0], text: "" })).toEqual({ selected_indexes: [0, 2] });
    expect(answerItem({ indexes: [], text: "just this" })).toEqual({ text: "just this" });
  });

  it("returns null only when the answer says nothing at all", () => {
    expect(answerItem(undefined)).toBeNull();
    expect(answerItem({ indexes: [], text: "   " })).toBeNull();
  });

  it("refuses a choice whose accompanying text the daemon would reject", () => {
    // The whole item is withheld, not just the text: a half-sent answer would
    // deliver a choice the reader never confirmed on its own.
    expect(answerItem({ indexes: [1], text: `a${String.fromCharCode(0x1b)}b` })).toBeNull();
  });
});

describe("answer plan", () => {
  it("keeps a multi-question batch atomic until every answer exists", () => {
    expect(buildAnswerPlan(QUESTION_BATCH, { "toolu_batch#0": { indexes: [1], text: "" } })).toBeNull();
    expect(
      buildAnswerPlan(QUESTION_BATCH, {
        "toolu_batch#0": { indexes: [1], text: "" },
        "toolu_batch#1": { indexes: [], text: " /health " },
      }),
    ).toEqual([{ selected_indexes: [1] }, { text: "/health" }]);
  });

  it("carries a mixed batch — a bare choice, a qualified choice, and bare text", () => {
    expect(
      buildAnswerPlan(QUESTION_BATCH, {
        "toolu_batch#0": { indexes: [0, 1], text: "both, but primary first" },
        "toolu_batch#1": { indexes: [], text: "/health\n/ready" },
      }),
    ).toEqual([
      { selected_indexes: [0, 1], text: "both, but primary first" },
      { text: "/health\n/ready" },
    ]);
  });

  it("refuses to build a plan from text the daemon would reject", () => {
    expect(
      buildAnswerPlan([QUESTION_BATCH[1]], {
        "toolu_batch#1": { indexes: [], text: `/health${String.fromCharCode(0x1b)}` },
      }),
    ).toBeNull();
  });

  it("holds the atomic group request until every individual card confirms", () => {
    const selections = {
      "toolu_batch#0": { indexes: [1], text: "" },
      "toolu_batch#1": { indexes: [], text: "/health" },
    };
    expect(confirmedGroupPlan(QUESTION_BATCH, selections, { "toolu_batch#0": true })).toBeNull();
    expect(
      confirmedGroupPlan(QUESTION_BATCH, selections, {
        "toolu_batch#0": true,
        "toolu_batch#1": true,
      }),
    ).toEqual([{ selected_indexes: [1] }, { text: "/health" }]);
  });

  it("toggles multiselect choices without dropping the other selections", () => {
    const first = toggleOption(undefined, 2, true);
    const second = toggleOption(first, 0, true);
    const third = toggleOption(second, 2, true);

    expect(first).toEqual({ indexes: [2], text: "" });
    expect(second).toEqual({ indexes: [2, 0], text: "" });
    expect(third).toEqual({ indexes: [0], text: "" });
  });

  it("keeps a choice and its qualification alive through each other's edits", () => {
    const typed = typeText({ indexes: [1], text: "" }, "but only the primary");
    expect(typed).toEqual({ indexes: [1], text: "but only the primary" });
    expect(toggleOption(typed, 0, true)).toEqual({
      indexes: [1, 0],
      text: "but only the primary",
    });
    expect(toggleOption(typed, 0, false)).toEqual({
      indexes: [0],
      text: "but only the primary",
    });
  });
});

describe("textError", () => {
  it("passes ordinary text, and an empty box is incomplete rather than invalid", () => {
    expect(textError("/health")).toBeNull();
    expect(textError("")).toBeNull();
    expect(textError("   ")).toBeNull();
  });

  it("accepts a line break and a tab — prose is what the daemon now takes", () => {
    // Named by CODE POINT, never as a literal or an escape: a control character
    // in this source would be invisible, which is the bug this rule exists for.
    for (const code of [0x09, 0x0a]) {
      expect(textError(`first${String.fromCharCode(code)}second`)).toBeNull();
    }
  });

  it("still rejects every other C0 byte and DEL", () => {
    // NUL, backspace, VT, CR, ESC, unit separator, DEL — the ends and the
    // interesting middle of what the daemon's own validator refuses.
    for (const code of [0x00, 0x08, 0x0b, 0x0d, 0x1b, 0x1f, 0x7f]) {
      expect(textError(`a${String.fromCharCode(code)}b`)).not.toBeNull();
    }
  });
});

describe("free text — offered on every kind now", () => {
  it("renders a secondary box beside a single-select's options", () => {
    expect(render([SINGLE_SELECT])).toContain('data-testid="question-custom"');
  });

  it("renders it beside a multiSelect too, which the daemon now accepts", () => {
    expect(render([MULTI_SELECT])).toContain('data-testid="question-custom"');
  });

  it("makes it the primary field where the question carries no options", () => {
    expect(render([QUESTION_BATCH[1]])).toContain('data-testid="question-text"');
  });

  it("is a textarea, because an input strips the line break out of a paste", () => {
    expect(render([SINGLE_SELECT])).toContain("<textarea");
  });
});

describe("option descriptions", () => {
  it("prints each description — it is what distinguishes two options", () => {
    const html = render([SINGLE_SELECT]);
    expect(html).toContain("Report only the primary connection.");
    expect(html).toContain("Fan out to every configured replica.");
    expect(html.match(/data-testid="question-option-description"/g)).toHaveLength(2);
  });

  it("keeps options structurally identical when none carry descriptions", () => {
    const bare: AgentQuestionView = {
      ...SINGLE_SELECT,
      options: [
        { label: "Yes", description: null },
        { label: "No", description: null },
      ],
    };
    const html = render([bare]);
    expect(html).not.toContain('data-testid="question-option-description"');
    expect(html.match(/data-testid="question-option"/g)).toHaveLength(2);
  });
});
