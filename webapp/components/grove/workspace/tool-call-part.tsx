"use client";

import { useState, type PropsWithChildren, type ReactNode } from "react";
import { useAuiState, type ToolCallMessagePartProps } from "@assistant-ui/react";
import { LoaderIcon } from "lucide-react";

import {
  ToolFallbackArgs,
  ToolFallbackContent,
  ToolFallbackRoot,
  ToolFallbackTrigger,
} from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { CardScroll } from "@/components/grove/card";
import {
  asToolCall,
  formatToolDuration,
  toolCallFields,
  toolCallStatus,
  toolCallStatusLabel,
  type ToolCallView,
} from "@/lib/grove/adapters";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./code-block";
import { NestLevel, nestedTriggerClass, useNestDepth } from "./nesting";
import { toolCommandLanguage } from "./selectors";
import type { ThreadComponents, ThreadGroupPart } from "./thread";

/**
 * A tool call, with the request, the response, the clock and the running state
 * the wire has been carrying all along.
 *
 * WHAT THIS REPLACES. The transcript rendered the vendored `ToolFallback` on a
 * part whose only content was a digest line — so a Bash call read as
 * "Used tool: Bash" with the command as its entire body, no arguments, no
 * output, no duration, and a check mark whether or not the call had finished.
 * Every one of those facts was already on `DigestEntryView.tool`; none of them
 * had a home in assistant-ui's native part, and the adapter carries them across
 * on `artifact` (see `adapters/transcript.ts` for why that slot).
 *
 * THE STATE FORK IS THREE-WAY, NOT TWO. `artifact` absent means this agent
 * reports no per-call detail; `status === "running"` means in flight; a settled
 * call with `result === null` ran and returned nothing. They render as three
 * different things on purpose — collapsing the last two is the bug this
 * component exists to avoid, and it is invisible in a screenshot.
 *
 * WHY IT COMPOSES `ToolFallback.*` RATHER THAN THE WHOLE `ToolFallback`. The
 * default reads its status from the message part, and assistant-ui derives a
 * tool part's status from its owning MESSAGE — which every Grove transcript
 * message pins to `complete` so a working agent still has a composer. The
 * compound sub-components take that status as a plain prop, which is the seam.
 * Nothing here restyles them.
 */
export function ToolCallPart(props: ToolCallMessagePartProps): ReactNode {
  const call = asToolCall(props.artifact);
  const status = toolCallStatus(call?.status ?? "ok");
  const duration = formatToolDuration(call?.duration_ms);
  const running = call?.status === "running";
  const failed = call?.status === "error";
  // Nesting depth is read, not passed in — see `./nesting` for why a context
  // is the only seam that reaches a component assistant-ui mounts for us.
  const depth = useNestDepth();

  return (
    <ToolFallbackRoot
      data-testid="tool-call"
      data-tool-status={call?.status ?? "unknown"}
      {...(call ? { "data-tool-use-id": call.tool_use_id } : {})}
    >
      <div className="flex min-w-0 items-center gap-2">
        <ToolFallbackTrigger
          toolName={call?.name || props.toolName}
          status={status}
          className={cn(nestedTriggerClass(depth), "shrink-0")}
        />
        {/* The digest line's remainder: the target, which is the one thing worth
            reading without opening anything. Mono because it is a literal — a
            path, a command, a pattern (design-system §3) — and truncated with
            the full value in `title`, never clipped without a way back. */}
        {props.argsText ? (
          <span
            className="text-content-tertiary min-w-0 flex-1 truncate font-mono text-xs"
            title={props.argsText}
          >
            {props.argsText}
          </span>
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        {/* A word, then the colour — never colour alone (design-system §4.7).
            A settled OK call says nothing here: the vendored check already did,
            and the duration beside it is the fact worth the pixels. */}
        {running || failed ? (
          <span
            className={cn(
              "shrink-0 text-xs",
              failed ? "text-destructive" : "text-content-tertiary",
            )}
            data-testid="tool-call-state"
          >
            {toolCallStatusLabel(call?.status ?? "ok")}
          </span>
        ) : null}
        {duration ? (
          <span
            className="text-content-tertiary shrink-0 text-xs tabular-nums"
            data-testid="tool-call-duration"
          >
            {duration}
          </span>
        ) : null}
      </div>
      <ToolFallbackContent>
        {/* The call's own request/response is one level deeper than the
            trigger it hangs off — the third rung the task names explicitly. */}
        <NestLevel>{call ? <ToolCallDetail call={call} /> : <NoDetail />}</NestLevel>
      </ToolFallbackContent>
    </ToolFallbackRoot>
  );
}

/**
 * The expanded body: request, response, and the correlation key that tells two
 * simultaneous calls apart.
 *
 * Exported because a closed `CollapsibleContent` is UNMOUNTED — so the only way
 * to pin what the body says about a given wire shape is to render it directly.
 * That is the contract worth pinning anyway; whether it sits behind a Radix
 * collapsible is the vendored component's business.
 */
export function ToolCallDetail({ call }: { call: ToolCallView }): ReactNode {
  const fields = toolCallFields(call.input);

  return (
    <>
      <section className="flex min-w-0 flex-col gap-1.5" data-testid="tool-call-request">
        <SectionLabel>Request</SectionLabel>
        {fields.length === 0 ? (
          <Absent>No arguments recorded.</Absent>
        ) : (
          fields.map((field) => (
            <div key={field.name} className="flex min-w-0 flex-col gap-1">
              <p className="text-content-tertiary font-mono text-xs">{field.name}</p>
              {field.value === "" ? (
                <Absent>Empty.</Absent>
              ) : (
                <ToolBody text={field.value} language={toolCommandLanguage(call.name, field.name)} />
              )}
            </div>
          ))
        )}
        {call.input_truncated ? <Truncated>Arguments were capped by the daemon.</Truncated> : null}
      </section>
      <section className="flex min-w-0 flex-col gap-1.5" data-testid="tool-call-response">
        <SectionLabel>Response</SectionLabel>
        {call.status === "running" ? (
          <Absent>Still running — no response yet.</Absent>
        ) : call.result === null || call.result === undefined ? (
          // NOT the same claim as "running", and the wire keeps them apart.
          <Absent>Returned nothing.</Absent>
        ) : (
          <ToolBody text={call.result} />
        )}
        {call.result_truncated ? <Truncated>Response was capped by the daemon.</Truncated> : null}
      </section>
      <p className="text-content-tertiary font-mono text-xs" data-testid="tool-call-id">
        {call.tool_use_id}
      </p>
    </>
  );
}

/**
 * A body of provider text, bounded.
 *
 * `ToolFallbackArgs` is the vendored code well — a `<pre>` with the surface,
 * radius and type already decided — and it takes the wrapper's `className`,
 * which is the only place a bound can land. `ToolFallbackResult` was the
 * obvious alternative and was rejected: it prints its own "Result:" heading
 * with no way to turn it off, which would have made the two halves of this body
 * asymmetric and duplicated the label above it.
 *
 * `max-h-*`, never `h-*` — the app has exactly one bounding idiom, and the
 * other one is how a silent clip shipped once already (webapp/CLAUDE.md).
 * `wrap-break-word` INHERITS into the `<pre>`, which is what stops a 4 KB
 * single-line command from bleeding the column the transcript shares with
 * everything else.
 *
 * `language` forks the well: a recognised command (`toolCommandLanguage`)
 * renders through `CodeBlock` instead, bounded the same `max-h-64` way via
 * `CardScroll` — one bounding idiom either branch takes, never a second one
 * invented for the highlighted case.
 */
function ToolBody({ text, language }: { text: string; language?: string }): ReactNode {
  if (language) {
    return (
      <CardScroll data-testid="tool-call-body">
        <CodeBlock code={text} language={language} />
      </CardScroll>
    );
  }
  return (
    <ToolFallbackArgs
      argsText={text}
      className="max-h-64 overflow-y-auto wrap-break-word"
      data-testid="tool-call-body"
    />
  );
}

function SectionLabel({ children }: PropsWithChildren): ReactNode {
  return <p className="text-content-secondary text-xs font-medium">{children}</p>;
}

/** An absence is never the loudest thing on a surface (design-system §11). */
function Absent({ children }: PropsWithChildren): ReactNode {
  return <p className="text-content-tertiary text-xs">{children}</p>;
}

/**
 * Truncation is STATED, never implied.
 *
 * `input_truncated` / `result_truncated` exist as explicit flags precisely
 * because an ellipsis inside a command's own stdout is indistinguishable from
 * output the tool actually produced — so a trailing "…" cannot be the signal.
 */
function Truncated({ children }: PropsWithChildren): ReactNode {
  return (
    <p className="text-content-tertiary text-xs" data-testid="tool-call-truncated">
      {children}
    </p>
  );
}

/** The provider reports no per-call detail. Distinct from "returned nothing",
 * and the difference is the user's: one is a gap in Grove's reporting, the
 * other is a fact about the call. */
function NoDetail(): ReactNode {
  return <Absent>This agent reports no request or response detail for its tool calls.</Absent>;
}

/**
 * A run of tool calls, with the live ones counted at the collapsed header.
 *
 * An agent issues several calls in one turn and the vendored grouping folds
 * them behind "3 tool calls" — which is the right shape, and would otherwise
 * hide the very state a user is watching for. The count of running and failed
 * calls rides the header, and a group follows its own liveness: it opens when a
 * call starts, so a spinner is never buried one click deep, and folds back up
 * when the last one settles.
 *
 * That is what makes "only the newest group is open" true without any group
 * needing to know its position. Running is a property one group has at a time
 * on a live workspace, so tracking it locally gives the same result an explicit
 * latest-index would, with no cross-group state to keep correct — and a
 * finished transcript opens nothing at all.
 *
 * The counts are read as NUMBERS from the store rather than as a summary
 * object: a selector returning a fresh object re-renders this on every frame of
 * an unrelated fleet tick, which is the cost model `transcript.tsx` documents.
 */
export function ToolCallGroup({
  group,
  children,
}: PropsWithChildren<{ group: ThreadGroupPart }>): ReactNode {
  const { indices } = group;
  const running = useAuiState((state) => countStatus(state.message.parts, indices, "running"));
  const failed = useAuiState((state) => countStatus(state.message.parts, indices, "error"));

  // The vendored `ToolFallback` uses exactly this shape to open itself when a
  // call starts requiring action: track the previous value, act on the edge.
  // BOTH edges, symmetrically — a rising edge that opens with no falling edge
  // that closes is not a disclosure, it is an append-only list of everything
  // the agent has ever done, and on a live workspace that is the whole
  // transcript held open until someone reloads the page. Following the edge
  // rather than the value is also what keeps the user in charge: a manual
  // toggle changes `open` without moving `live`, so nothing fires and their
  // choice stands until the group's own state actually changes.
  const live = running > 0;
  const [open, setOpen] = useState(live);
  const [wasLive, setWasLive] = useState(live);
  if (live !== wasLive) {
    setWasLive(live);
    setOpen(live);
  }

  return (
    <ToolGroupRoot variant="ghost" open={open} onOpenChange={setOpen} data-testid="tool-call-group">
      <div className="flex min-w-0 items-center gap-2">
        <ToolGroupTrigger count={indices.length} active={live} className="shrink-0" />
        {running > 0 ? (
          <span className="text-content-tertiary shrink-0 text-xs tabular-nums">
            {running} running
          </span>
        ) : null}
        {failed > 0 ? (
          <span className="text-destructive shrink-0 text-xs tabular-nums">{failed} failed</span>
        ) : null}
      </div>
      <ToolGroupContent>
        {/* Each call in the group is one level deeper than the group's own
            trigger — the first of the two boundaries the task names. */}
        <NestLevel>{children}</NestLevel>
      </ToolGroupContent>
    </ToolGroupRoot>
  );
}

function countStatus(
  parts: readonly { type: string; artifact?: unknown }[],
  indices: readonly number[],
  want: ToolCallView["status"],
): number {
  let total = 0;
  for (const index of indices) {
    const part = parts[index];
    if (part?.type !== "tool-call") continue;
    if (asToolCall(part.artifact)?.status === want) total += 1;
  }
  return total;
}

/**
 * The invocation facts for a card that owns its own disclosure.
 *
 * A `file_edit` entry carries `tool` too — the diff is the PAYLOAD and the
 * invocation is a separate fact about it — but that card already expands into
 * its diff, so a second expander would be two disclosures on one row. It gets
 * the header line instead: spinner or duration, and any truncation stated.
 *
 * The spinner is lucide's `LoaderIcon` with `animate-spin`, which is the exact
 * pair both `ToolFallbackTrigger` and `ToolGroupTrigger` draw; the registry
 * ships no standalone spinner component to compose here.
 */
export function ToolInvocationMeta({ tool }: { tool: ToolCallView | null }): ReactNode {
  if (!tool) return null;
  const duration = formatToolDuration(tool.duration_ms);
  const running = tool.status === "running";
  const failed = tool.status === "error";
  const truncated = tool.input_truncated || tool.result_truncated;

  return (
    <span
      className="text-content-tertiary flex shrink-0 items-center gap-1.5 text-xs"
      data-testid="tool-invocation-meta"
      data-tool-status={tool.status}
    >
      {running ? (
        <LoaderIcon
          aria-hidden
          className="size-3 shrink-0 animate-spin [animation-duration:0.6s]"
        />
      ) : null}
      {running || failed ? (
        <span className={cn(failed && "text-destructive")}>{toolCallStatusLabel(tool.status)}</span>
      ) : null}
      {duration ? <span className="tabular-nums">{duration}</span> : null}
      {truncated ? <span>capped</span> : null}
    </span>
  );
}

/**
 * The transcript's component overrides, as one value.
 *
 * Every surface that renders `Thread` passes this same object — the workspace
 * pane and the archived-session route both. A second set of overrides is how
 * one transcript renderer becomes two, which is the bug the session route's own
 * docstring warns about.
 */
export const GROVE_THREAD_COMPONENTS: ThreadComponents = {
  ToolFallback: ToolCallPart,
  ToolGroup: ToolCallGroup,
};
