import type { ComponentProps } from "react";

import type { ApprovalCard } from "@/components/elements/approval-card";
import type { ElicitationField, ElicitationForm } from "@/components/elements/elicitation-form";
import type { AgentQuestionView } from "@/lib/grove/api";

/**
 * An agent's question → props for the vendored question surfaces.
 *
 * `AskUserQuestion` yields a BATCH of select/free-text questions sharing one
 * `group_id`, answered atomically in one POST. So the unit of presentation is
 * the group, not the question, and it renders as one `ElicitationForm` whose
 * fields are the batch.
 *
 * `plan_approval` is deliberately NOT handled here. A plan is a Markdown
 * document plus the agent dialog's own rows, which `ApprovalCard` cannot show —
 * it renders its `command` in a `font-mono text-xs` block with no whitespace
 * handling, capped `max-w-sm`, and offers no slot for per-option consequences.
 * `components/grove/workspace/plan-approval.tsx` owns that surface instead.
 *
 * Pure: the caller supplies the callbacks and owns the submit.
 */

/** The data half of `ApprovalCard`'s props — the consumer owns the handlers. */
export type ApprovalCardProps = Pick<
  ComponentProps<typeof ApprovalCard>,
  "state" | "command" | "title" | "subtitle"
>;

/** The data half of `ElicitationForm`'s props — the consumer owns the handlers. */
export type ElicitationFormProps = Pick<
  ComponentProps<typeof ElicitationForm>,
  "server" | "message" | "fields" | "state"
>;

/** One question group, resolved to the vendored element that fits it. */
export type QuestionPresentation =
  | ({ kind: "approval"; groupId: string } & ApprovalCardProps)
  | ({ kind: "elicitation"; groupId: string } & ElicitationFormProps);

/** The asker, when the wire recorded no source tool. */
const UNKNOWN_SOURCE = "agent";

/**
 * Resolve one question group to its presentation, or null for an empty group.
 *
 * `questions` must share a `group_id` — the batch is one interaction. Grouping
 * a mixed list is {@link questionGroups}' job.
 */
export function questionPresentation(
  questions: readonly AgentQuestionView[],
): QuestionPresentation | null {
  const first = questions[0];
  if (!first) return null;

  // A lone optionless confirm still has no form fields worth drawing, so it
  // keeps the approval shape. `plan_approval` never reaches here — it has its
  // own surface (see the module docstring) — which is exactly why it is a
  // separate kind rather than a `confirm` a reader has to disambiguate by tool.
  if (questions.length === 1 && first.kind === "confirm") {
    return {
      kind: "approval",
      groupId: first.group_id,
      // `denied` and `running` have no wire counterpart: Grove learns only that
      // a group resolved, never how. Both settle as `done`.
      state: first.answered ? "done" : "request",
      command: first.prompt,
      title: first.header ?? "Approve this plan",
      subtitle: first.source_tool || UNKNOWN_SOURCE,
    };
  }

  return {
    kind: "elicitation",
    groupId: first.group_id,
    server: first.source_tool || UNKNOWN_SOURCE,
    // A batch has no shared prompt — each question's own prompt is its field
    // label instead, so nothing is stated twice and nothing is invented.
    message: questions.length === 1 ? first.prompt : "",
    fields: questions.map(elicitationField),
    state: questions.every((question) => question.answered) ? "accepted" : "request",
  };
}

/** Split a mixed question list into its groups, preserving wire order. One
 * `AskUserQuestion` call is one group; a session can have several pending. */
export function questionGroups(
  questions: readonly AgentQuestionView[],
): AgentQuestionView[][] {
  const groups = new Map<string, AgentQuestionView[]>();
  for (const question of questions) {
    const group = groups.get(question.group_id);
    if (group) group.push(question);
    else groups.set(question.group_id, [question]);
  }
  return [...groups.values()];
}

function elicitationField(question: AgentQuestionView): ElicitationField {
  const options = question.options.map((option) => option.label);
  return {
    // The wire id is the answer-back address, so it is also the field identity.
    name: question.id,
    label: question.header ?? question.prompt,
    // Grove records the resolved answer as one blob per group, so an unanswered
    // question shows empty rather than a fabricated default.
    value: question.answer ?? "",
    kind: options.length > 0 ? "choice" : "text",
    ...(options.length > 0 ? { options } : {}),
  };
}
