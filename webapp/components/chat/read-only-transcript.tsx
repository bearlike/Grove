"use client";

import type { CSSProperties } from "react";
import { MessagesSquare } from "lucide-react";
import { AssistantRuntimeProvider, ThreadPrimitive } from "@assistant-ui/react";
import { ErrorBoundary } from "@/components/error-boundary";
import { GroveMessage } from "@/components/chat/chat-message";
import { useTranscriptRuntime } from "@/lib/grove/assistant-runtime";
import type { SessionTurnView } from "@/lib/grove/types";
import { cn } from "@/lib/utils";

/**
 * A recorded conversation, rendered read-only (the Session Catalog's drill-in).
 * The SAME assistant-ui headless path `ChatPanel` uses — Grove's own
 * external-store runtime (`useTranscriptRuntime`) under
 * `ThreadPrimitive.Viewport`/`.Messages`, every message drawn by the one
 * `GroveMessage` — with everything that implies a live agent removed: no
 * composer, no interrupt, no live pending-question card, no todo card, no
 * steering notice.
 *
 * That subtraction is the whole contract, and it is structural rather than
 * disabled-by-prop: a catalog session usually has no workspace, so there is
 * nothing on the other end to steer. Rendering a greyed-out composer would
 * promise an affordance the wire cannot honor.
 *
 * Heaviest leaf on its page (streamdown + assistant-ui, via `GroveMessage`) —
 * import it lazily, exactly as the session page imports `ChatPanel`.
 *
 * Test seams: `read-only-transcript`, `read-only-transcript-empty`, plus every
 * `chat-message`/`tool-group`/`chat-question` seam `GroveMessage` already owns.
 */
export function ReadOnlyTranscript({ turns }: { turns: SessionTurnView[] }) {
  return (
    <ErrorBoundary>
      <ReadOnlyTranscriptInner turns={turns} />
    </ErrorBoundary>
  );
}

/**
 * The reading measure. A read-only transcript never shares its row with a work
 * panel, so it takes the wide single-pane measure `ChatPanel` uses in that same
 * situation (`THREAD_MAX_WIDTH_WIDE`) rather than the split-view 44rem.
 */
const THREAD_TOKENS: CSSProperties = {
  "--thread-max-width": "80rem",
} as CSSProperties;

/** The one shared column measure — mirrors `chat-panel.tsx`'s `CHAT_COLUMN` so
 *  a transcript reads identically whichever surface renders it. */
const CHAT_COLUMN = "mx-auto w-full max-w-[var(--thread-max-width)] px-3 sm:px-4 lg:px-6";

function ReadOnlyTranscriptInner({ turns }: { turns: SessionTurnView[] }) {
  const { runtime, itemCount } = useTranscriptRuntime({ turns });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div
        data-testid="read-only-transcript"
        className="flex min-h-0 min-w-0 flex-1 flex-col"
        style={THREAD_TOKENS}
      >
        {/* The Viewport is itself the scroll container (assistant-ui drives
            scrollTop on it) — `role="log"` is the Playwright scroll seam, same
            as the steer panel's. */}
        <ThreadPrimitive.Viewport
          role="log"
          className="relative min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          <div className={cn(CHAT_COLUMN, "flex min-w-0 flex-col py-3")}>
            {itemCount === 0 ? (
              <EmptyTranscript />
            ) : (
              <ThreadPrimitive.Messages components={{ Message: GroveMessage }} />
            )}
          </div>
        </ThreadPrimitive.Viewport>
      </div>
    </AssistantRuntimeProvider>
  );
}

/** A session whose transcript exists but records no turns — a real, honest
 *  outcome (an agent launched and never prompted), not an error. */
function EmptyTranscript() {
  return (
    <div
      data-testid="read-only-transcript-empty"
      className="flex flex-col items-center justify-center gap-2 p-6 text-center"
    >
      <MessagesSquare aria-hidden className="size-6 text-muted-foreground/70" />
      <h3 className="text-sm font-medium">No conversation recorded</h3>
      <p className="text-sm text-muted-foreground">
        This session&apos;s transcript has no turns yet.
      </p>
    </div>
  );
}
