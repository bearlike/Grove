"use client";

import { useState } from "react";

import { field } from "@/components/elements/surfaces";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PendingQuestionGroup } from "@/lib/grove/runtime";
import type { AgentQuestionView, QuestionAnswerItem } from "@/lib/grove/api";
import {
  answerItem,
  confirmedGroupPlan,
  selectionOf,
  textError,
  toggleOption,
  typeText,
  type Selection,
} from "./answer-plan";
import { PlanApproval } from "./plan-approval";
import { QuestionCard, QuestionOptions, selectionSummary } from "./question-card";

/**
 * The live questions in the transcript footer.
 *
 * A question is confirmed one at a time, matching the reader's mental model
 * and the one-card history shape. The current write contract nevertheless
 * accepts exactly one ordered answer list for the whole tool call, so confirming
 * the last complete card dispatches that one group request. The UI never
 * pretends each confirmation reached the agent independently.
 *
 * EVERY KIND IS ANSWERABLE FROM HERE. It used to be select-only, because the
 * answer was typed into the provider's picker and that picker accepted free
 * text on a single-select and nowhere else. The daemon now dismisses the picker
 * and delivers the batch back as prose, so the widget's grammar constrains
 * nothing: a `multi_select` genuinely posts, a `free_text` has a field, and a
 * `confirm` — which carries no options at all — is answered in words.
 */
export function PendingQuestion({
  group,
  onSubmit,
  submitting,
}: {
  group: PendingQuestionGroup;
  onSubmit: (groupId: string, answers: QuestionAnswerItem[]) => void;
  submitting: boolean;
}) {
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [confirmed, setConfirmed] = useState<Record<string, true>>({});

  const choose = (question: AgentQuestionView, index: number): void => {
    setSelections((current) => ({
      ...current,
      [question.id]: toggleOption(current[question.id], index, question.multiselect),
    }));
    setConfirmed((current) => without(current, question.id));
  };

  const type = (question: AgentQuestionView, text: string): void => {
    setSelections((current) => ({ ...current, [question.id]: typeText(current[question.id], text) }));
    setConfirmed((current) => without(current, question.id));
  };

  const confirm = (question: AgentQuestionView): void => {
    if (!answerItem(selections[question.id])) return;
    const nextConfirmed = { ...confirmed, [question.id]: true as const };
    setConfirmed(nextConfirmed);

    // The delivery API has no per-question address: a tool_use_id plus an
    // ordered full group is the only expressible write. Confirmation remains
    // local until every card has been explicitly confirmed.
    const plan = confirmedGroupPlan(group.questions, selections, nextConfirmed);
    if (plan) onSubmit(group.groupId, plan);
  };

  // Answering a plan picks a row in the agent's own dialog, so the click IS the
  // answer: one option, submitted immediately, with no local Confirm step. A
  // plan is never batched with other questions (`ExitPlanMode` is its own call),
  // so this group is always the whole payload.
  const choosePlan = (question: AgentQuestionView, index: number): void => {
    if (submitting) return;
    onSubmit(group.groupId, [{ selected_indexes: [index] }]);
  };

  return (
    <div className="flex w-full flex-col gap-3" data-testid="pending-question">
      {group.questions.map((question) => {
        const selection = selections[question.id];
        const isConfirmed = confirmed[question.id] === true;
        const state = submitting ? "submitting" : isConfirmed ? "confirmed" : "pending";

        if (question.kind === "plan_approval") {
          return (
            <PlanApproval
              key={question.id}
              question={question}
              disabled={submitting}
              onChoose={(index) => choosePlan(question, index)}
            />
          );
        }

        return (
          <QuestionCard key={question.id} question={question} state={state}>
            {isConfirmed ? (
              <ConfirmedAnswer
                question={question}
                selection={selection}
                onChange={() => setConfirmed((current) => without(current, question.id))}
              />
            ) : (
              <QuestionEditor
                question={question}
                selection={selection}
                disabled={submitting}
                onChoose={(index) => choose(question, index)}
                onType={(text) => type(question, text)}
                onConfirm={() => confirm(question)}
              />
            )}
          </QuestionCard>
        );
      })}
    </div>
  );
}

function QuestionEditor({
  question,
  selection,
  disabled,
  onChoose,
  onType,
  onConfirm,
}: {
  question: AgentQuestionView;
  selection: Selection | undefined;
  disabled: boolean;
  onChoose: (index: number) => void;
  onType: (text: string) => void;
  onConfirm: () => void;
}) {
  const { text } = selectionOf(selection);
  const error = textError(text);
  const complete = answerItem(selection) !== null;

  return (
    <>
      <QuestionOptions question={question} selection={selection} onChoose={onChoose} disabled={disabled} />
      {question.kind === "confirm" && <QuestionStance onPick={onType} disabled={disabled} />}
      <QuestionTextAnswer
        question={question}
        value={text}
        error={error}
        onChange={onType}
        disabled={disabled}
      />
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={disabled || !complete}
          onClick={onConfirm}
          data-testid="question-confirm"
        >
          Confirm answer
        </Button>
      </div>
    </>
  );
}

/**
 * Approve / reject for a `confirm`, whose answer the wire carries as TEXT.
 *
 * An `ExitPlanMode` confirm offers no options, so there is no index to send and
 * a choice control here would be a claim about a payload that does not exist.
 * These are TEMPLATES for the field below: clicking one writes the sentence the
 * daemon will actually deliver, and the field stays editable — so "approve, but
 * do the migration last" is one click plus typing, rather than a second control
 * that can disagree with the text beside it. Picking a stance replaces whatever
 * is in the field, because the field IS the answer and two of them cannot both
 * be it.
 */
const STANCES = [
  { label: "Approve", text: "Yes, go ahead with this plan." },
  { label: "Reject", text: "No, do not go ahead with this plan." },
] as const;

function QuestionStance({
  onPick,
  disabled,
}: {
  onPick: (text: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {STANCES.map((stance) => (
        <Button
          key={stance.label}
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onPick(stance.text)}
          data-testid="question-stance"
        >
          {stance.label}
        </Button>
      ))}
    </div>
  );
}

/**
 * The free-text answer, offered on every question kind.
 *
 * A TEXTAREA rather than an input, and that is a wire fact rather than a taste:
 * the daemon now accepts newlines and tabs, and an `<input>` strips a line
 * break out of a paste before any handler sees it — so a single-line control
 * would make the widened contract unreachable. It reproduces the vendored
 * elicitation form's own field treatment (this file is registered as that
 * component's port), differing only in being editable.
 */
function QuestionTextAnswer({
  question,
  value,
  error,
  onChange,
  disabled,
}: {
  question: AgentQuestionView;
  value: string;
  error: string | null;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const errorId = `question-error-${question.id}`;
  // A question with options gets a secondary box: the options are the answer
  // and this qualifies them. One with none gets the primary field.
  const custom = question.options.length > 0;
  return (
    <>
      <textarea
        value={value}
        rows={custom ? 2 : 3}
        placeholder={placeholderFor(question)}
        disabled={disabled}
        aria-label={question.header ?? question.prompt}
        aria-invalid={error !== null}
        {...(error ? { "aria-describedby": errorId } : {})}
        onChange={(event) => onChange(event.target.value)}
        data-testid={custom ? "question-custom" : "question-text"}
        className={cn(
          field,
          "text-foreground/80 placeholder:text-foreground/35 focus-visible:ring-foreground/20 w-full resize-y rounded-lg px-2.5 py-1.5 text-xs outline-none focus-visible:ring-1 disabled:opacity-50",
        )}
      />
      {error && (
        <span id={errorId} role="alert" data-testid="question-error" className="text-destructive text-xs">
          {error}
        </span>
      )}
    </>
  );
}

function placeholderFor(question: AgentQuestionView): string {
  if (question.options.length > 0) return "Add anything the options don't cover";
  if (question.kind === "confirm") return "Approve, reject, or say what to change";
  return "Type your answer";
}

function ConfirmedAnswer({
  question,
  selection,
  onChange,
}: {
  question: AgentQuestionView;
  selection: Selection | undefined;
  onChange: () => void;
}) {
  if (!selection) return null;
  return (
    <div className="flex items-center justify-between gap-3">
      <span data-testid="question-confirmed-answer" className="min-w-0 text-sm text-content-secondary">
        {selectionSummary(question, selection)}
      </span>
      <Button type="button" variant="outline" size="sm" onClick={onChange} data-testid="question-change">
        Change
      </Button>
    </div>
  );
}

function without<T extends Record<string, unknown>>(value: T, key: string): T {
  const { [key]: _discarded, ...rest } = value;
  return rest as T;
}
