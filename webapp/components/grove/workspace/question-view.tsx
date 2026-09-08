"use client";

import type { AgentQuestionView } from "@/lib/grove/api";
import { PlanApproval } from "./plan-approval";
import { QuestionCard, ResolvedQuestion } from "./question-card";

/**
 * One resolved question from the transcript.
 *
 * The question's prompt is the always-visible disclosure summary. The detail
 * contains its original choices (including descriptions) and the provider's
 * recorded group result. It deliberately has no action callbacks: history
 * reports a settled interaction and cannot offer dead controls.
 *
 * A settled plan keeps its own surface rather than degrading to the question
 * card — the plan is still a document, and reading back what was approved is the
 * main reason to scroll to it. Omitting `onChoose` is what makes it inert, so
 * the read-only shape cannot drift from the live one.
 */
export function HistoricalQuestion({ question }: { question: AgentQuestionView }) {
  if (question.kind === "plan_approval") {
    return <PlanApproval question={question} />;
  }
  return (
    <QuestionCard question={question} state="resolved">
      <ResolvedQuestion question={question} />
    </QuestionCard>
  );
}
