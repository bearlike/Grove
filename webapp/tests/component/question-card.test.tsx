import { StrictMode } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionCard, PendingQuestionCard } from "@/components/workspace/question-card";
import { GroveProtocolError } from "@/lib/grove/client";
import { refusalNotice } from "@/lib/grove/steering-notice";
import type { AgentQuestionView } from "@/lib/grove/types";

function question(over: Partial<AgentQuestionView> = {}): AgentQuestionView {
  return {
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
    ...over,
  };
}

describe("QuestionCard (read-only, epic #74 — untouched)", () => {
  it("renders a static option list, no button/input, unanswered", () => {
    render(<QuestionCard question={question()} />);
    const card = screen.getByTestId("question-card");
    expect(card.dataset.answered).toBe("false");
    expect(screen.getAllByTestId("question-option")).toHaveLength(2);
    expect(card.querySelector("button")).toBeNull();
    expect(card.querySelector("input")).toBeNull();
    expect(screen.getByTestId("question-pending")).toHaveTextContent("awaiting answer");
  });
});

describe("PendingQuestionCard — a lone single-select question", () => {
  it("renders option buttons with label + description and auto-submits on one tap", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <PendingQuestionCard
        questions={[question()]}
        onSubmit={onSubmit}
        submitting={false}
        error={null}
      />,
    );

    const buttons = screen.getAllByTestId("question-option-button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveTextContent("Big-bang cutover");
    expect(buttons[0]).toHaveTextContent("Faster, riskier");

    await user.click(buttons[1]);
    expect(onSubmit).toHaveBeenCalledWith([{ selected_indexes: [1] }]);
    // No explicit submit control for the no-review lone case.
    expect(screen.queryByTestId("question-submit")).toBeNull();
  });

  it("does not double-dispatch onSubmit under React.StrictMode (regression, #111 review)", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <StrictMode>
        <PendingQuestionCard
          questions={[question()]}
          onSubmit={onSubmit}
          submitting={false}
          error={null}
        />
      </StrictMode>,
    );

    const buttons = screen.getAllByTestId("question-option-button");
    await user.click(buttons[1]);
    // StrictMode double-invokes a functional setState updater to surface
    // impurities; the auto-submit dispatch must live outside it so a single
    // tap still yields exactly one POST.
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith([{ selected_indexes: [1] }]);
  });

  it("answers via the 'Other' free-text input on Enter, single-select only", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <PendingQuestionCard
        questions={[question()]}
        onSubmit={onSubmit}
        submitting={false}
        error={null}
      />,
    );

    const input = screen.getByTestId("question-other-input");
    await user.type(input, "Kiwi{Enter}");
    expect(onSubmit).toHaveBeenCalledWith([{ text: "Kiwi" }]);
  });

  it("renders an optionless free_text-kind question as the Other input alone", () => {
    render(
      <PendingQuestionCard
        questions={[question({ kind: "free_text", options: [] })]}
        onSubmit={vi.fn()}
        submitting={false}
        error={null}
      />,
    );
    expect(screen.queryByTestId("question-option-button")).toBeNull();
    expect(screen.getByTestId("question-other-input")).toBeInTheDocument();
  });
});

describe("PendingQuestionCard — multiSelect", () => {
  const multi = question({
    id: "g-2#0",
    kind: "multi_select",
    multiselect: true,
    prompt: "Toppings?",
    options: [
      { label: "Cheese", description: null },
      { label: "Mushrooms", description: null },
    ],
  });

  it("toggles checkboxes and requires an explicit disabled-until-ready submit, no Other input", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <PendingQuestionCard questions={[multi]} onSubmit={onSubmit} submitting={false} error={null} />,
    );

    expect(screen.queryByTestId("question-other-input")).toBeNull();
    const submit = screen.getByTestId("question-submit");
    expect(submit).toBeDisabled();

    const checkboxes = screen.getAllByTestId("question-checkbox");
    await user.click(checkboxes[0]);
    await user.click(checkboxes[1]);
    expect(onSubmit).not.toHaveBeenCalled(); // toggling never auto-submits

    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith([{ selected_indexes: [0, 1] }]);
  });
});

describe("PendingQuestionCard — a multi-question group", () => {
  const color = question({ id: "g-3#0", prompt: "Color?" });
  const toppings = question({
    id: "g-3#1",
    kind: "multi_select",
    multiselect: true,
    prompt: "Toppings?",
    options: [
      { label: "Cheese", description: null },
      { label: "Mushrooms", description: null },
    ],
  });

  it("answers all questions, disables submit until every question is answered, then one submit", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <PendingQuestionCard
        questions={[color, toppings]}
        onSubmit={onSubmit}
        submitting={false}
        error={null}
      />,
    );

    const submit = screen.getByTestId("question-submit");
    expect(submit).toBeDisabled();

    const optionButtons = screen.getAllByTestId("question-option-button");
    // First question's second option ("Incremental").
    await user.click(optionButtons[1]);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(submit).toBeDisabled();

    const checkboxes = screen.getAllByTestId("question-checkbox");
    await user.click(checkboxes[0]);
    expect(submit).toBeEnabled();

    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith([{ selected_indexes: [1] }, { selected_indexes: [0] }]);
  });
});

describe("PendingQuestionCard — submitting + error states", () => {
  it("disables controls while a submit is in flight", () => {
    render(
      <PendingQuestionCard
        questions={[question({ multiselect: true, kind: "multi_select" })]}
        onSubmit={vi.fn()}
        submitting
        error={null}
      />,
    );
    for (const el of screen.getAllByTestId("question-checkbox")) {
      expect(el).toBeDisabled();
    }
    expect(screen.getByTestId("question-submit")).toBeDisabled();
  });

  it("surfaces a 409/422 error inline, then falls back to the read-only pending card", () => {
    render(
      <PendingQuestionCard
        questions={[question()]}
        onSubmit={vi.fn()}
        submitting={false}
        error="Steering unavailable — stale question"
      />,
    );
    expect(screen.getByTestId("question-error")).toHaveTextContent(
      "Steering unavailable — stale question",
    );
    // Degraded to the exact read-only card — no interactive controls survive.
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByTestId("question-option-button")).toBeNull();
    expect(screen.queryByTestId("question-submit")).toBeNull();
  });

  it("surfaces a 422 error inline via the shared refusal formatter, then falls back to read-only", () => {
    const err = new GroveProtocolError("invalid_answer", "answer index out of range", 422);
    render(
      <PendingQuestionCard
        questions={[question()]}
        onSubmit={vi.fn()}
        submitting={false}
        error={refusalNotice(err, "answer")}
      />,
    );
    // 422 isn't a typed 409/501 refusal, so the shared formatter falls back
    // to its generic wording — same shape the chat panel and turns view both
    // now render via the one shared helper.
    expect(screen.getByTestId("question-error")).toHaveTextContent(
      "Could not submit the answer — answer index out of range",
    );
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByTestId("question-option-button")).toBeNull();
    expect(screen.queryByTestId("question-submit")).toBeNull();
  });
});

describe("PendingQuestionCard — confirm (ExitPlanMode) kind", () => {
  it("has no keystroke driver yet (core CLAUDE.md) — renders read-only, not interactive", () => {
    render(
      <PendingQuestionCard
        questions={[question({ kind: "confirm", options: [], prompt: "Approve this plan?" })]}
        onSubmit={vi.fn()}
        submitting={false}
        error={null}
      />,
    );
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByTestId("question-option-button")).toBeNull();
    expect(screen.queryByTestId("question-other-input")).toBeNull();
  });
});
