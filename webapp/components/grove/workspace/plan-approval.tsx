"use client";

import { lazy, Suspense } from "react";
import { TextMessagePartProvider } from "@assistant-ui/react";
import { ClipboardCheckIcon } from "lucide-react";

import { CardScroll, CardShell } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import type { AgentQuestionView } from "@/lib/grove/api";
import { NewTabLinks } from "./new-tab-links";

const MarkdownText = lazy(() =>
  import("@/components/assistant-ui/markdown-text").then((module) => ({ default: module.MarkdownText })),
);

/**
 * A plan approval: the plan as a document, and the agent's OWN choices beneath it.
 *
 * This is not a `QuestionCard`, and the split is structural rather than
 * cosmetic. That card puts its prompt in the disclosure SUMMARY — inside a
 * `<button>` — which is right for a one-sentence ask and impossible here twice
 * over: a plan is a multi-KB Markdown document, and block-level Markdown
 * (`<p>`, `<ul>`, `<pre>`) inside a `<button>` is invalid HTML. So the plan is a
 * body, never a header.
 *
 * The options are the dialog's real rows, supplied by the engine
 * (`PLAN_APPROVAL_OPTIONS`), and picking one sends its INDEX. Approving a plan
 * is a mode transition only the agent's own dialog can perform, so Grove renders
 * what that dialog offers instead of inventing an Approve/Reject pair whose
 * "approval" was really a sentence typed into a composer. Each row states its
 * consequence, because "approve" and "approve and stop asking for this session"
 * are different acts.
 */
export function PlanApproval({
  question,
  onChoose,
  disabled = false,
  chosen,
}: {
  question: AgentQuestionView;
  onChoose?: (index: number) => void;
  disabled?: boolean;
  chosen?: number;
}) {
  const interactive = onChoose !== undefined;
  return (
    <CardShell data-testid="plan-approval" data-question-kind={question.kind}>
      <div className="flex min-w-0 flex-col gap-3 p-3">
        <div className="flex items-center gap-2 text-sm font-medium text-content-primary">
          <ClipboardCheckIcon aria-hidden className="size-4 shrink-0 text-content-tertiary" />
          <span>Plan approval</span>
        </div>
        <PlanBody plan={question.prompt} />
        {interactive ? (
          <PlanChoices
            question={question}
            onChoose={onChoose}
            disabled={disabled}
            chosen={chosen}
          />
        ) : (
          <ResolvedPlanAnswer question={question} />
        )}
      </div>
    </CardShell>
  );
}

/**
 * The plan itself, rendered as the Markdown it is.
 *
 * Reuses `AgentMessageBody`'s proven composition — `TextMessagePartProvider`
 * feeds the unmodified vendored renderer a bare string with no assistant-ui
 * runtime in scope. `CardScroll` bounds it because a plan is read *before* a
 * decision and the choices must stay reachable without scrolling past a
 * document; that is the opposite of `ToolBody`'s case, where the reader already
 * opened a disclosure to read one artifact end to end.
 */
function PlanBody({ plan }: { plan: string }) {
  return (
    <CardScroll className="max-h-[32rem]">
      <NewTabLinks
        className="min-w-0 break-words text-base text-content-primary"
        data-testid="plan-body"
      >
        <TextMessagePartProvider text={plan}>
          <Suspense fallback={<p className="whitespace-pre-wrap">{plan}</p>}>
            <MarkdownText />
          </Suspense>
        </TextMessagePartProvider>
      </NewTabLinks>
    </CardScroll>
  );
}

/**
 * The dialog's rows as buttons.
 *
 * One click is the whole answer — there is no separate Confirm, because the
 * choice IS the act and a second step would only invite the reader to think the
 * first one did nothing. The index is what travels; the label is Grove's own
 * wording for a row whose rendered text is not a contract.
 */
function PlanChoices({
  question,
  onChoose,
  disabled,
  chosen,
}: {
  question: AgentQuestionView;
  onChoose: (index: number) => void;
  disabled: boolean;
  chosen?: number;
}) {
  return (
    <ul className="flex flex-col gap-2" aria-label="How to answer this plan">
      {question.options.map((option, index) => (
        <li key={option.label} className="flex min-w-0 flex-col gap-1">
          <Button
            type="button"
            variant={chosen === index ? "default" : "outline"}
            size="sm"
            disabled={disabled}
            aria-pressed={chosen === index}
            onClick={() => onChoose(index)}
            data-testid="plan-option"
            className="h-auto min-h-8 justify-start whitespace-normal"
          >
            {option.label}
          </Button>
          {option.description && (
            <span data-testid="plan-option-description" className="text-xs text-content-secondary">
              {option.description}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A settled plan states the group result the provider recorded, never a guess. */
function ResolvedPlanAnswer({ question }: { question: AgentQuestionView }) {
  return (
    <p data-testid="plan-answer" className="text-sm text-content-secondary">
      <span className="font-medium text-content-primary">Answer: </span>
      {question.answer?.trim() || "Answered"}
    </p>
  );
}
