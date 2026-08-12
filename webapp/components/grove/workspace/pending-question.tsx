"use client";

/**
 * Grove's live question form — a PORT of
 * `components/elements/elicitation-form.tsx`, kept diffable against it line for
 * line.
 *
 * WHY a port and not a composition: the vendored form renders choice, toggle
 * and text values as display-only spans. Its only callbacks are the form-level
 * Send and Decline actions, so no prop can collect or change a field value.
 *
 * THE RULE FOR EDITING THIS FILE: only the deltas below may diverge from the
 * vendored original. Everything else is upstream's anatomy, typography,
 * spacing, colour and surface treatment.
 *
 *   1. Fields are controls: choice spans become `button`s with `aria-pressed`,
 *      and text spans become controlled `input`s. Grove's local `Selection`
 *      state supplies their values and preserves multiselect toggling.
 *   2. Send follows Grove's wire grammar: the whole batch submits atomically;
 *      a lone single-select submits on its tap, a lone free-text question on
 *      Enter, and multi-question/multiselect batches use one Send button. There
 *      is no Decline control because the daemon accepts only complete answers.
 *      The mutation remains outside every state updater so React cannot
 *      double-POST it in development.
 *   3. The shell is full-width (`max-w-none`) like the composed historical form,
 *      and retains Grove's existing `pending-question`, `question-option` and
 *      `question-submit` test seams.
 *   4. An option carries its `description`. The vendored field models options as
 *      bare `string`s, so the description has nowhere to go upstream — but it is
 *      the whole reason two options are distinguishable, so a described option
 *      set stacks vertically and prints the sentence under the label. An
 *      undescribed set keeps upstream's wrapping pill row exactly.
 *   5. A single-select question also offers an answer the agent did not list
 *      (`acceptsCustomText`), because the picker's synthetic "Type something."
 *      option is real and reachable. Its text is validated against the daemon's
 *      own control-character rule, inline, so a paste never becomes a 422.
 *
 * `components/elements/elicitation-form.tsx` stays in place, unmodified: it is
 * the oracle this file is diffed against, and `registry:check` verifies it.
 */

import { useState } from "react";
import { PlugIcon } from "lucide-react";

import { field, inkButton, mono, paper } from "@/components/elements/surfaces";
import { cn } from "@/lib/utils";
import type { PendingQuestionGroup } from "@/lib/grove/runtime";
import type { AgentQuestionView, QuestionAnswerItem } from "@/lib/grove/api";
import { QuestionView } from "./question-view";
import {
  acceptsCustomText,
  buildAnswerPlan,
  needsExplicitSubmit,
  textError,
  toggleOption,
  type Selection,
} from "./answer-plan";

/**
 * The LIVE question batch — the one place in this surface the user answers the
 * agent rather than reading it.
 *
 * A `confirm` batch (an agent asking to proceed with its plan) has no options on
 * the wire and no answer shape the daemon accepts, so it degrades to the
 * read-only approval card: the human answers it in the terminal.
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

  if (group.presentation.kind === "approval" || submitting) {
    return <QuestionView presentation={group.presentation} />;
  }

  const plan = buildAnswerPlan(group.questions, selections);
  const explicit = needsExplicitSubmit(group.questions);

  // The mutation fires OUTSIDE any state updater: React runs updaters twice in
  // development, which would double-POST an answer.
  const choose = (question: AgentQuestionView, index: number): void => {
    const next = {
      ...selections,
      [question.id]: toggleOption(selections[question.id], index, question.multiselect),
    };
    setSelections(next);
    if (!explicit) {
      const immediate = buildAnswerPlan(group.questions, next);
      if (immediate) onSubmit(group.groupId, immediate);
    }
  };

  const type = (question: AgentQuestionView, text: string): void => {
    setSelections((current) => ({ ...current, [question.id]: { kind: "text", text } }));
  };

  // Enter sends only where a tap would have: a batch with a review step waits
  // for Send, exactly as the terminal does.
  const submitOnEnter = explicit ? undefined : () => plan && onSubmit(group.groupId, plan);

  return (
    <div
      data-slot="elicitation-form"
      data-testid="pending-question"
      className={cn(paper, "flex w-full max-w-none flex-col gap-3.5 rounded-[20px] p-4")}
    >
      <div className="flex items-center gap-2.5">
        <span className="bg-foreground/[0.05] text-foreground/45 flex size-7 shrink-0 items-center justify-center rounded-lg">
          <PlugIcon className="size-3.5" />
        </span>
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">
          {group.presentation.server}
        </span>
        <span className={cn(mono, "text-foreground/30 shrink-0")}>needs input</span>
      </div>

      <p className="text-foreground/55 text-xs leading-relaxed">{group.presentation.message}</p>

      <div className="flex flex-col gap-2.5">
        {group.questions.map((question) => {
          const labelId = `question-label-${question.id}`;
          const selection = selections[question.id];
          const described = question.options.some((option) => option.description);
          return (
            <div key={question.id} className="flex flex-col gap-1">
              <span id={labelId} className={cn(mono, "text-foreground/35")}>
                {question.header ?? question.prompt}
              </span>
              {question.options.length > 0 ? (
                <>
                  <div
                    role="group"
                    aria-labelledby={labelId}
                    className={cn("flex gap-1.5", described ? "flex-col" : "flex-wrap")}
                  >
                    {question.options.map((option, index) => {
                      const chosen = isChosen(selection, index);
                      return (
                        <button
                          type="button"
                          key={option.label}
                          aria-pressed={chosen}
                          onClick={() => choose(question, index)}
                          data-testid="question-option"
                          className={cn(
                            "text-xs transition-colors",
                            described
                              ? "flex flex-col items-start gap-0.5 rounded-xl px-2.5 py-1.5 text-left"
                              : "rounded-full px-2.5 py-1",
                            chosen
                              ? "bg-foreground text-background"
                              : cn(field, "text-foreground/55"),
                          )}
                        >
                          <span>{option.label}</span>
                          {option.description && (
                            <span
                              data-testid="question-option-description"
                              className={cn(
                                "leading-relaxed",
                                chosen ? "text-background/70" : "text-foreground/40",
                              )}
                            >
                              {option.description}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  {acceptsCustomText(question) && (
                    <TextAnswer
                      question={question}
                      selection={selection}
                      // The agent's own options are the answer it expects, so
                      // this reads as the alternative it is rather than a
                      // second, equal field.
                      placeholder="Or type your own answer"
                      testid="question-custom"
                      onType={type}
                      {...(submitOnEnter ? { onEnter: submitOnEnter } : {})}
                    />
                  )}
                </>
              ) : (
                <TextAnswer
                  question={question}
                  selection={selection}
                  placeholder={question.prompt}
                  testid="question-text"
                  labelledBy={labelId}
                  onType={type}
                  {...(submitOnEnter ? { onEnter: submitOnEnter } : {})}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="flex h-8 items-center justify-end gap-2">
        {explicit && (
          <button
            type="button"
            disabled={plan === null}
            onClick={() => plan && onSubmit(group.groupId, plan)}
            data-testid="question-submit"
            className={cn(inkButton, "flex h-8 items-center rounded-full px-3.5 text-xs font-medium")}
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * One typed answer, with the daemon's own refusal stated beside it.
 *
 * Shared by the optionless field and delta 5's custom answer so the two cannot
 * validate differently — the whole point of checking client-side is that the
 * message is the same one the 422 would have carried.
 */
function TextAnswer({
  question,
  selection,
  placeholder,
  testid,
  labelledBy,
  onType,
  onEnter,
}: {
  question: AgentQuestionView;
  selection: Selection | undefined;
  placeholder: string;
  testid: string;
  labelledBy?: string;
  onType: (question: AgentQuestionView, text: string) => void;
  onEnter?: () => void;
}) {
  const value = textOf(selection);
  const error = textError(value);
  const errorId = `question-error-${question.id}`;
  return (
    <>
      <input
        value={value}
        placeholder={placeholder}
        aria-invalid={error !== null}
        {...(labelledBy ? { "aria-labelledby": labelledBy } : { "aria-label": placeholder })}
        {...(error ? { "aria-describedby": errorId } : {})}
        onChange={(event) => onType(question, event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || !onEnter) return;
          event.preventDefault();
          onEnter();
        }}
        data-testid={testid}
        className={cn(field, "text-foreground/80 rounded-lg px-2.5 py-1.5 text-xs")}
      />
      {error && (
        <span id={errorId} role="alert" data-testid="question-error" className="text-destructive text-xs">
          {error}
        </span>
      )}
    </>
  );
}

function isChosen(selection: Selection | undefined, index: number): boolean {
  return selection?.kind === "indexes" && selection.indexes.includes(index);
}

function textOf(selection: Selection | undefined): string {
  return selection?.kind === "text" ? selection.text : "";
}
