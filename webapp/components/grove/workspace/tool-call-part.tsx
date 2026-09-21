"use client";

import { useMemo, useState, type PropsWithChildren, type ReactNode } from "react";
import { useToolBody } from "@/lib/grove/hooks";
import { useAuiState, type ToolCallMessagePartProps } from "@assistant-ui/react";
import { LoaderIcon } from "lucide-react";

import {
  ToolFallbackArgs,
} from "@/components/assistant-ui/tool-fallback";
import {
  asToolCall,
  formatToolDuration,
  shouldFetchToolBody,
  toolCallFields,
  toolCallStatusLabel,
  type ToolCallView,
} from "@/lib/grove/adapters";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./code-block";
import { AgentMessage } from "./agent-message";
import { outgoingAgentMessage, type AgentMessageData } from "@/lib/grove/adapters/agent-message";
import { NestLevel } from "./nesting";
import { FileEditDiff, fileEditCounts } from "./file-edit-part";
import type { FileEditPartData } from "@/lib/grove/adapters";
import { ToolTimeline, ToolTimelineStep } from "./tool-timeline";
import { compactToolTarget, toolPresentation, toolTimelineStats, toolTimelineSummary } from "@/lib/grove/adapters/tool-catalog";
import { useToolIcons } from "@/lib/grove/tool-icons";
import { toolCommandLanguage } from "./selectors";
import type { ThreadComponents, ThreadGroupPart } from "./thread";

/**
 * The incoming-delivery envelope the adapter attached to this part, if any.
 *
 * Read defensively rather than by widening `ToolCallMessagePartProps`: the prop
 * type is assistant-ui's, and `groveMailbox` is a key Grove adds on its own
 * parts (see `mailboxPart`). A narrow local read keeps that coupling in one
 * place instead of asserting a vendored type is something it is not.
 */
function incomingMailbox(props: ToolCallMessagePartProps): AgentMessageData | null {
  const carried = (props as { groveMailbox?: AgentMessageData }).groveMailbox;
  return carried && typeof carried.body === "string" ? carried : null;
}

/** The diff the adapter attached to an edit call, read the same defensive way. */
function carriedFileEdit(props: ToolCallMessagePartProps): FileEditPartData | null {
  const carried = (props as { groveFileEdit?: FileEditPartData }).groveFileEdit;
  return carried && typeof carried.displayPath === "string" ? carried : null;
}

/** Cataloged actions retain the provider request/response and native per-call identity. */
export function ToolCallPart(props: ToolCallMessagePartProps): ReactNode {
  const call = asToolCall(props.artifact);
  const running = call?.status === "running";
  const serverIcons = useToolIcons();
  // Mirrors the step's own disclosure, declared ahead of every early return per
  // the rules of hooks. The vendored root would happily own this state
  // uncontrolled, but then this component could not say whether the body is on
  // screen — which is the one fact the withheld-body fetch is gated on.
  const [open, setOpen] = useState(false);
  const presentation = toolPresentation(call?.name || props.toolName, call?.input, props.argsText, serverIcons);
  // An INCOMING delivery the adapter folded into this run. It is not a tool
  // call, so it draws the card alone — there is no invocation to report a
  // delivery response for, and inventing that section would claim this session
  // sent something it received.
  const incoming = incomingMailbox(props);
  if (incoming) return <AgentMessage message={incoming} />;

  const message = call ? outgoingAgentMessage(call) : null;
  if (message && call) return (
    <AgentMessage message={message}>
      <section className="mt-3 flex min-w-0 flex-col gap-1.5 border-t border-border pt-2" data-testid="mailbox-delivery">
        <SectionLabel>Delivery response</SectionLabel>
        {call.result ? <ToolBody text={call.result} /> : <Absent>{running ? "Sending — no response yet." : "No response recorded."}</Absent>}
      </section>
    </AgentMessage>
  );

  // An EDIT step names its file and expands into the native split diff — the
  // artifact a reader opening an edit came for. Its request/response detail
  // would be the same diff restated as two text blobs, so the diff replaces it.
  const edit = carriedFileEdit(props);

  return (
    <ToolTimelineStep
      {...presentation}
      summary={edit ? edit.displayPath.split(/[\\/]/).filter(Boolean).at(-1) ?? edit.displayPath : compactToolTarget(presentation)}
      name={call?.name || props.toolName}
      running={running}
      status={call?.status ?? "unknown"}
      callId={call?.tool_use_id}
      // A withheld body is fetched when the DISCLOSURE opens, never on mount:
      // that is the whole point of the daemon's projection, and the step is the
      // only component that knows its own open state.
      onOpenChange={setOpen}
      metadata={
        <>
          {edit ? <FileEditCounts data={edit} /> : null}
          <ToolInvocationMeta tool={call} />
        </>
      }
    >
      {edit ? (
        <FileEditDiff data={edit} />
      ) : (
        <NestLevel>{call ? <ToolCallDetail call={call} open={open} /> : <NoDetail />}</NestLevel>
      )}
    </ToolTimelineStep>
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
export function ToolCallDetail({
  call,
  open = true,
}: {
  call: ToolCallView;
  /** Whether the disclosure holding this body is on screen. Defaults true so a
   * direct render (the unit tests, any future always-open surface) still shows
   * the body; only the FETCH is gated on it. */
  open?: boolean;
}): ReactNode {
  // The fetch lives in a SEPARATE component rather than a hook here, and the
  // reason is structural: a hook on this component would subscribe every tool
  // body in the transcript to react-query — thousands of them on a long
  // session, all to serve the handful a reader opens — and would make an
  // inline body unrenderable without a QueryClient at all. Only the `available`
  // branch mounts a subscriber.
  return call.body === "available" ? (
    <WithheldToolCallBody call={call} open={open} />
  ) : (
    <ToolCallBody call={call} />
  );
}

/**
 * The `available` case: fetch on open, then render the same body inline would.
 *
 * Mounted only for a withheld call, so the query is the exception rather than
 * the rule — and it renders the fetched view through {@link ToolCallBody}, so
 * an opened body and an inline one cannot drift apart.
 */
function WithheldToolCallBody({
  call,
  open,
}: {
  call: ToolCallView;
  open: boolean;
}): ReactNode {
  const fetched = useToolBody(call.tool_use_id, shouldFetchToolBody(call.body, open));
  if (fetched.data) return <ToolCallBody call={fetched.data} />;
  return (
    <ToolCallBody
      call={call}
      // A withheld body is neither absent nor empty — saying "Returned nothing"
      // here would state a fact about the call that only the drill-in can
      // answer, which is the confusion `body` exists to end.
      pendingNote={fetched.isError ? "Couldn’t load this body." : "Loading…"}
    />
  );
}

function ToolCallBody({
  call,
  pendingNote,
}: {
  call: ToolCallView;
  /** Set only while a withheld body has not arrived; replaces both sections'
   * content without claiming anything about the call. */
  pendingNote?: string;
}): ReactNode {
  const fields = toolCallFields(call.input);
  const pending = pendingNote !== undefined;

  return (
    <>
      <section className="flex min-w-0 flex-col gap-1.5" data-testid="tool-call-request">
        <SectionLabel>Request</SectionLabel>
        {pending ? (
          <Absent>{pendingNote}</Absent>
        ) : fields.length === 0 ? (
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
      </section>
      <section className="flex min-w-0 flex-col gap-1.5" data-testid="tool-call-response">
        <SectionLabel>Response</SectionLabel>
        {call.status === "running" ? (
          <Absent>Still running — no response yet.</Absent>
        ) : pending ? (
          <Absent>{pendingNote}</Absent>
        ) : call.result === null || call.result === undefined ? (
          // NOT the same claim as "running", and the wire keeps them apart.
          <Absent>Returned nothing.</Absent>
        ) : (
          <ToolBody text={call.result} />
        )}
      </section>
      <p className="text-content-tertiary font-mono text-xs" data-testid="tool-call-id">
        {call.tool_use_id}
      </p>
    </>
  );
}

/**
 * A body of provider text, WHOLE.
 *
 * `ToolFallbackArgs` is the vendored code well — a `<pre>` with the surface,
 * radius and type already decided — and it takes the wrapper's `className`.
 * `ToolFallbackResult` was the obvious alternative and was rejected: it prints
 * its own "Result:" heading with no way to turn it off, which would have made
 * the two halves of this body asymmetric and duplicated the label above it.
 *
 * **No height bound, deliberately — this is the one place `CardScroll` is the
 * wrong answer.** That idiom bounds a LIST, where the reader scans rows and
 * the tenth is worth no more than the first. A tool response is one artifact
 * read end to end (the tail of a build log, a diff, a test summary), and it
 * only renders at all inside a disclosure that is closed by default — so the
 * reader has already said they want it. A 16rem porthole onto a 20 KB body,
 * nested inside the transcript's own scroller, hides content exactly the way
 * the daemon's old character cap did, and costs a scroll trap on top.
 *
 * `wrap-break-word` INHERITS into the `<pre>`, which is what stops a 4 KB
 * single-line command from bleeding the column the transcript shares with
 * everything else. `min-w-0` on the highlighted branch does the same job for
 * `CodeBlock`, whose own `<pre>` wraps rather than scrolls.
 */
function ToolBody({ text, language }: { text: string; language?: string }): ReactNode {
  if (language) {
    return (
      <div className="min-w-0" data-testid="tool-call-body">
        <CodeBlock code={text} language={language} />
      </div>
    );
  }
  return (
    <ToolFallbackArgs
      argsText={text}
      className="wrap-break-word"
      data-testid="tool-call-body"
    />
  );
}

/**
 * One edit's `+n −n`, on the step's own row.
 *
 * The same `FileRowSummary` vocabulary the standalone card uses, minus the
 * path — the step already names the file, and repeating it would put the same
 * word twice on one line.
 */
function FileEditCounts({ data }: { data: FileEditPartData }): ReactNode {
  const { added, removed } = fileEditCounts(data);
  return (
    <span className="flex shrink-0 items-center gap-1.5 text-xs tabular-nums" data-testid="file-edit-counts">
      <span className="text-success">+{added}</span>
      <span className="text-destructive">−{removed}</span>
    </span>
  );
}

function SectionLabel({ children }: PropsWithChildren): ReactNode {
  return <p className="text-content-secondary text-xs font-medium">{children}</p>;
}

/** An absence is never the loudest thing on a surface (design-system §11). */
function Absent({ children }: PropsWithChildren): ReactNode {
  return <p className="text-content-tertiary text-xs">{children}</p>;
}

/** The provider reports no per-call detail. Distinct from "returned nothing",
 * and the difference is the user's: one is a gap in Grove's reporting, the
 * other is a fact about the call. */
function NoDetail(): ReactNode {
  return <Absent>This agent reports no request or response detail for its tool calls.</Absent>;
}

/** Native tool grouping keeps identity and chronological boundaries owned by assistant-ui. */
export function ToolCallGroup({
  group,
  children,
}: PropsWithChildren<{ group: ThreadGroupPart }>): ReactNode {
  const { indices } = group;
  const parts = useAuiState(state => state.message.parts);
  const serverIcons = useToolIcons();
  const { summary, stats } = useMemo(() => {
    const steps = indices.flatMap(index => {
      const part = parts[index];
      if (part?.type !== "tool-call") return [];
      const call = asToolCall(part.artifact);
      const step = toolPresentation(call?.name || part.toolName, call?.input, part.argsText, serverIcons);
      // The diff is the only honest source for a change count — the tool name
      // says an edit happened, never how much of the file moved. An edit whose
      // payload the provider did not report contributes a step and no chip.
      const edit = (part as { groveFileEdit?: FileEditPartData }).groveFileEdit;
      if (!edit) return [step];
      return [{ ...step, fileStat: { file: edit.path, ...fileEditCounts(edit) } }];
    });
    return { summary: toolTimelineSummary(steps), stats: toolTimelineStats(steps) };
  }, [parts, indices, serverIcons]);
  const running = countStatus(parts, indices, "running");
  const failed = countStatus(parts, indices, "error");
  const live = running > 0;
  // Tool runs flush into their own assistant message. `parts.length` therefore
  // says only whether another PART follows, while `message.isLast` says whether
  // another MESSAGE has objectively arrived. A call that just completed is still
  // live until that boundary, so it stays open across the gap before the next
  // call; a historical tail stays closed because it never went live here.
  const isLast = useAuiState(state => state.message.isLast);
  const [observedLive, setObservedLive] = useState(live);
  if (live && !observedLive) setObservedLive(true);
  const active = isLast && (live || observedLive);
  const [wasActive, setWasActive] = useState(active);
  const [open, setOpen] = useState(active);
  // Manual toggles survive additional calls; only the run's boundary closes it.
  if (active !== wasActive) {
    setWasActive(active);
    setOpen(active);
  }

  return (
    <ToolTimeline
      open={open}
      onOpenChange={setOpen}
      streaming={live}
      label={summary.label}
      icons={summary.icons}
      stats={stats}
      status={<>
        {running > 0 ? <span className="text-content-tertiary shrink-0 text-xs tabular-nums">{running} running</span> : null}
        {failed > 0 ? <span className="text-destructive shrink-0 text-xs tabular-nums">{failed} failed</span> : null}
      </>}
    >
      <NestLevel>{children}</NestLevel>
    </ToolTimeline>
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
 * the header line instead: spinner or duration.
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
