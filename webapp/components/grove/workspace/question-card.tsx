"use client";

import { useState, type ReactNode } from "react";

import { CardDisclosure, CardShell } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import type { AgentQuestionView } from "@/lib/grove/api";
import type { Selection } from "./answer-plan";

/**
 * The one question surface shared by the live footer and resolved transcript.
 *
 * A group is the delivery unit, but a question is the reading and answering
 * unit. Keeping this shell per question means either context exposes the same
 * affordance: its prompt always remains in the disclosure header, while its
 * choices, answer, and controls are detail that may be collapsed away.
 */
export function QuestionCard({
  question,
  state,
  children,
}: {
  question: AgentQuestionView;
  state: "pending" | "confirmed" | "submitting" | "resolved";
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);

  return (
    <CardShell
      data-testid="question-card"
      data-question-kind={question.kind}
      data-question-state={state}
      data-collapsed={!open}
    >
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        header
        data-testid="question-toggle"
        summary={<span className="min-w-0 flex-1 text-sm font-medium">{question.prompt}</span>}
      >
        <div className="flex min-w-0 flex-col gap-3 p-3">{children}</div>
      </CardDisclosure>
    </CardShell>
  );
}

/**
 * The choice list has one rendering for live and resolved questions so an
 * option's description cannot disappear merely because its question settled.
 */
export function QuestionOptions({
  question,
  selection,
  onChoose,
  disabled = false,
}: {
  question: AgentQuestionView;
  selection?: Selection;
  onChoose?: (index: number) => void;
  disabled?: boolean;
}) {
  if (question.options.length === 0) return null;
  const selected = selection?.indexes ?? [];
  const interactive = onChoose !== undefined;

  return (
    <ul className="flex flex-col gap-2" aria-label={question.header ?? question.prompt}>
      {question.options.map((option, index) => {
        const chosen = selected.includes(index);
        return (
          <li key={`${option.label}-${index}`} className="flex min-w-0 flex-col gap-1">
            {interactive ? (
              <Button
                type="button"
                variant={chosen ? "default" : "outline"}
                size="sm"
                aria-pressed={chosen}
                disabled={disabled}
                onClick={() => onChoose(index)}
                data-testid="question-option"
                className="h-auto min-h-8 justify-start whitespace-normal"
              >
                {option.label}
              </Button>
            ) : (
              <span data-testid="question-option">{option.label}</span>
            )}
            {option.description && (
              <span data-testid="question-option-description" className="text-xs text-content-secondary">
                {option.description}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The wire stores a group-level result string, never synthetic per-option data. */
export function ResolvedQuestion({ question }: { question: AgentQuestionView }) {
  return (
    <>
      <QuestionOptions question={question} />
      <p data-testid="question-answer" className="text-sm text-content-secondary">
        <span className="font-medium text-content-primary">Answer: </span>
        {question.answer?.trim() || "Answered"}
      </p>
    </>
  );
}

/**
 * Read a local answer back without pretending the daemon has already resolved it.
 *
 * Choices and free text are joined rather than one winning, because they are
 * now sent together — a summary showing only the option would hide the
 * qualification the reader is about to submit.
 */
export function selectionSummary(question: AgentQuestionView, selection: Selection): string {
  const chosen = selection.indexes
    .map((index) => question.options[index]?.label ?? `Option ${index + 1}`)
    .join(", ");
  return [chosen, selection.text.trim()].filter(Boolean).join(" — ");
}
