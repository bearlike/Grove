"use client";

import { ApprovalCard } from "@/components/elements/approval-card";
import { ElicitationForm } from "@/components/elements/elicitation-form";
import { questionPresentation } from "@/lib/grove/adapters";
import type { QuestionPresentation } from "@/lib/grove/adapters";
import type { AgentQuestionView } from "@/lib/grove/api";

/**
 * A question as it reads once it is no longer live — in the transcript, or
 * while an answer is in flight.
 *
 * Both vendored elements are display-only for the option list (their choice
 * chips are spans, not controls), which is exactly right here and exactly wrong
 * for a live batch; see `pending-question.tsx` for the interactive twin.
 */
export function QuestionView({ presentation }: { presentation: QuestionPresentation }) {
  if (presentation.kind === "approval") {
    const { kind: _kind, groupId: _groupId, ...props } = presentation;
    return <ApprovalCard {...props} className="max-w-none" data-testid="question-approval" />;
  }
  const { kind: _kind, groupId: _groupId, ...props } = presentation;
  return <ElicitationForm {...props} className="max-w-none" data-testid="question-form" />;
}

/** One historical question from the transcript. */
export function HistoricalQuestion({ question }: { question: AgentQuestionView }) {
  const presentation = questionPresentation([question]);
  return presentation ? <QuestionView presentation={presentation} /> : null;
}
