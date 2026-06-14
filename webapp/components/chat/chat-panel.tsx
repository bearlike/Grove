"use client";

import { useState } from "react";
import { BellIcon, ChevronDownIcon, SquareIcon } from "lucide-react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ai-elements/prompt-input";
import { Response } from "@/components/ai-elements/response";
import { ToolGroup } from "@/components/ai-elements/tool";
import { ErrorBoundary } from "@/components/error-boundary";
import { RoleLabel } from "@/components/shared/role-label";
import { Button } from "@/components/ui/button";
import { chatItemsFromTurns, type ChatItem } from "@/lib/grove/chat-turns";
import { cn } from "@/lib/utils";
import { GroveProtocolError } from "@/lib/grove/client";
import {
  useInterrupt,
  useSendMessage,
  useSessionTurns,
  useWorkspaceSessions,
} from "@/lib/grove/hooks";

/**
 * The steer-capable chat surface on `/w/[id]` (issue #38): the latest
 * session's transcript rendered through the vendored AI Elements, plus a
 * composer that POSTs `/workspaces/{id}/message` and a WORKING-gated
 * interrupt. Heaviest leaf on the page (streamdown) — the page loads it via
 * `next/dynamic`, so always import this module lazily.
 *
 * Test seams: `chat-panel`, `chat-message` + `data-role` (each carrying a
 * `role-label` speaker tag), `tool-group`, `chat-tool` (inside an expanded
 * group), `chat-notification`, `chat-composer`, `chat-interrupt`,
 * `chat-notice`.
 */
export function ChatPanel({ workspaceId }: { workspaceId: string }) {
  // Self-wrapped boundary: a malformed streamed turn degrades to one
  // placeholder tile, never a white-screened detail page.
  return (
    <ErrorBoundary>
      <ChatPanelInner workspaceId={workspaceId} />
    </ErrorBoundary>
  );
}

/** Chat-tier transcript cadence — a conversation surface someone is watching. */
const CHAT_TURNS_REFETCH_MS = 5_000;

function ChatPanelInner({ workspaceId }: { workspaceId: string }) {
  const { data: sessions } = useWorkspaceSessions(workspaceId);
  // Newest-first on the wire — the head session is the one being steered,
  // same convention as the dashboard's displayState.
  const active = sessions?.[0] ?? null;
  const sessionId = active?.session_id ?? null;
  const agentState = active?.activity.state ?? "unknown";

  const { data: detail } = useSessionTurns(workspaceId, sessionId, CHAT_TURNS_REFETCH_MS);
  const send = useSendMessage(workspaceId, sessionId);
  const interrupt = useInterrupt(workspaceId);

  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const items = chatItemsFromTurns(detail?.turns ?? []);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || send.isPending) return;
    setNotice(null);
    setText("");
    send.mutate(message, { onError: (err) => setNotice(refusalNotice(err, "send")) });
  };

  const handleInterrupt = () => {
    setNotice(null);
    interrupt.mutate(undefined, { onError: (err) => setNotice(refusalNotice(err, "interrupt")) });
  };

  // Mirror the dashboard live-toggle: the affordance exists only while the
  // agent is actually WORKING — interrupting an idle agent is a daemon 409.
  const canInterrupt = agentState === "working";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2" data-testid="chat-panel">
      {/* The wrapper owns the height policy; the Conversation is an absolute
          fill so the transcript's intrinsic height can never propagate up the
          flex/grid chain. (A flex-1 Conversation did: with the page chain
          intrinsically sized — min-h page, fr grid rows — the percentage
          flex-basis resolves to CONTENT, so a long transcript grew the page
          instead of scrolling the pane. The vertical twin of the min-w-0
          lesson.) Below lg the page doesn't flex-fill, so the wrapper takes a
          viewport-proportional `h-[68dvh]` (a `min-h` floor keeps it usable on
          short/landscape screens): a fixed `h-80` wasted the lower half of a
          tall phone below the composer — the transcript now claims that space.
          On lg the wrapper flex-fills the viewport chain with a min-h floor. */}
      <div className="relative h-[68dvh] min-h-[20rem] min-w-0 overflow-hidden rounded-md border border-border lg:h-auto lg:min-h-[24rem] lg:flex-1">
        <Conversation className="absolute inset-0">
          <ConversationContent>
            {items.length === 0 ? (
              <ConversationEmptyState
                title="No conversation yet"
                description="Send a message to steer the agent."
              />
            ) : (
              items.map((item, i) => <ChatItemRow key={i} item={item} />)
            )}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      </div>

      {notice && (
        <p
          data-testid="chat-notice"
          role="status"
          className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
        >
          {notice}
        </p>
      )}

      <PromptInput data-testid="chat-composer" onSubmit={handleSubmit}>
        <PromptInputTextarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Steer the agent…"
          aria-label="Message to the agent"
        />
        <PromptInputToolbar>
          {canInterrupt && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Interrupt agent"
              data-testid="chat-interrupt"
              disabled={interrupt.isPending}
              onClick={handleInterrupt}
              className="text-[var(--status-error)] hover:text-[var(--status-error)]"
            >
              <SquareIcon className="size-4" />
            </Button>
          )}
          <PromptInputSubmit
            status={send.isPending ? "submitted" : "ready"}
            disabled={send.isPending || !text.trim()}
          />
        </PromptInputToolbar>
      </PromptInput>
    </div>
  );
}

function ChatItemRow({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case "message":
      // Alignment + bubble already separate the speakers; the IRC-style label
      // (shared convention with the TUI and TurnsView) makes the role legible
      // without scanning layout — right-aligned with the user bubble, leading
      // the assistant text.
      return (
        <Message from={item.role} data-testid="chat-message" data-role={item.role}>
          <RoleLabel
            role={item.role === "user" ? "you" : "agent"}
            className={item.role === "user" ? "-mb-1 self-end" : "-mb-1 self-start"}
          />
          <MessageContent>
            {item.role === "assistant" ? <Response>{item.text}</Response> : item.text}
          </MessageContent>
        </Message>
      );
    case "tools":
      // A consecutive run of digest tool calls — one collapsed "N tool calls"
      // row; the per-call Tool blocks live inside, behind the disclosure.
      return <ToolGroup calls={item.calls} data-testid="tool-group" />;
    case "note":
      // summary/status digest rows have no AI Elements equivalent — quiet
      // house-styled muted rows, same treatment as TurnsView gives them.
      return (
        <p
          data-testid="chat-note"
          data-tone={item.tone}
          className="text-center text-xs italic text-muted-foreground"
        >
          {item.text}
        </p>
      );
    case "notification":
      // A background-task notice the agent received — an event, not speech
      // (no bubble) and not run commentary (not a note): a quiet labeled row,
      // the subagent's full result behind a disclosure.
      return <NotificationRow summary={item.summary} detail={item.detail} />;
    case "continuation":
      return <p className="text-center text-xs italic text-muted-foreground">continued session</p>;
  }
}

function NotificationRow({ summary, detail }: { summary: string; detail: string }) {
  const [open, setOpen] = useState(false);
  const hasDetail = detail.length > 0;

  return (
    <div
      data-testid="chat-notification"
      className="w-full min-w-0 rounded-md border border-dashed border-border px-2 py-1.5"
    >
      <button
        type="button"
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full min-w-0 items-center gap-2 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          hasDetail && "transition-colors hover:text-foreground",
        )}
      >
        <BellIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{summary}</span>
        {hasDetail && (
          <ChevronDownIcon
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        )}
      </button>
      {hasDetail && open && (
        <p className="mt-1.5 break-words border-t border-border pt-1.5 text-xs text-muted-foreground">
          {detail}
        </p>
      )}
    </div>
  );
}

/** Map a steering failure to a quiet inline notice — refusals are expected. */
function refusalNotice(err: unknown, verb: "send" | "interrupt"): string {
  if (err instanceof GroveProtocolError && (err.status === 409 || err.status === 501)) {
    // The daemon's typed refusal (agent not running / adapter can't steer).
    return `Steering unavailable — ${err.message}`;
  }
  const message = err instanceof Error ? err.message : String(err);
  return `Could not ${verb === "send" ? "send the message" : "interrupt the agent"} — ${message}`;
}
